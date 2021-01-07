from dataclasses import dataclass
from typing import Optional

from brownie import interface, web3
from brownie.network.contract import InterfaceContainer

from yearn import constants, curve, uniswap


@dataclass
class VaultV1:
    vault: InterfaceContainer
    controller: InterfaceContainer
    token: interface.ERC20
    strategy: str
    is_wrapped: bool
    is_delegated: bool
    # the rest is populated in post init
    name: Optional[str] = None
    decimals: Optional[int] = None

    def __post_init__(self):
        self.vault = constants.VAULT_INTERFACES.get(self.vault, interface.yVault)(self.vault)
        self.controller = constants.CONTROLLER_INTERFACES[self.controller](self.controller)
        self.strategy = constants.STRATEGY_INTERFACES[self.strategy](self.strategy)
        self.token = interface.ERC20(self.token)
        self.name = constants.VAULT_ALIASES[str(self.vault)]
        self.decimals = self.vault.decimals()  # vaults inherit decimals from token

    def describe(self):
        scale = 10 ** self.decimals
        info = {
            "vault balance": self.vault.balance() / scale,
            "share price": self.vault.getPricePerFullShare() / 1e18,
            "vault total": self.vault.totalSupply() / scale,
            "strategy balance": self.strategy.balanceOf() / scale,
        }

        # some of the oldest vaults don't implement these methods
        if hasattr(self.vault, "available"):
            info["available"] = self.vault.available() / scale

        if hasattr(self.vault, "min") and hasattr(self.vault, "max"):
            info["strategy buffer"] = self.vault.min() / self.vault.max()

        # new curve voter proxy vaults
        if hasattr(self.strategy, "proxy"):
            vote_proxy = interface.CurveYCRVVoter(self.strategy.voter())
            # curve swap is broken across several strategies
            swap = interface.CurveSwap(constants.CURVE_SWAP_OVERRIDES.get(self.strategy._name, self.strategy.curve()))
            gauge = interface.CurveGauge(self.strategy.gauge())
            info.update(curve.calculate_boost(gauge, vote_proxy))
            info.update(curve.calculate_apy(gauge, swap))
            info["earned"] = gauge.claimable_tokens.call(vote_proxy).to("ether")

        if hasattr(self.strategy, "earned"):
            info["lifetime earned"] = self.strategy.earned() / scale

        if self.strategy._name == "StrategyYFIGovernance":
            ygov = interface.YearnGovernance(self.strategy.gov())
            info["earned"] = ygov.earned(self.strategy) / 1e18
            info["reward rate"] = ygov.rewardRate() / 1e18
            info["ygov balance"] = ygov.balanceOf(self.strategy) / 1e18
            info["ygov total"] = ygov.totalSupply() / 1e18

        if "token price" not in info:
            if self.name in ["aLINK"]:
                info["token price"] = uniswap.price_router(self.vault.underlying(), uniswap.usdc)
            elif self.name in ["USDC", "TUSD", "DAI", "USDT"]:
                info["token price"] = 1
            else:
                info["token price"] = uniswap.price_router(self.token, uniswap.usdc)

        info["tvl"] = info["vault balance"] * info["token price"]
        return info


def load_registry(address="registry.ychad.eth"):
    return interface.YRegistry(web3.ens.resolve(address))


def load_vaults(registry):
    vaults = []
    for vault in registry.getVaults():
        try:
            vaults.append(VaultV1(vault, *registry.getVaultInfo(vault)))
        except ValueError:
            print(f"failed to query vault: {vault}")
    return vaults