import asyncio
from anchorpy import Wallet
from driftpy.drift_client import DriftClient
from driftpy.account_subscription_config import AccountSubscriptionConfig
from driftpy.accounts import get_spot_market_account
from solana.rpc.async_api import AsyncClient

async def test():
    conn = AsyncClient("https://api.mainnet-beta.solana.com")
    client = DriftClient(conn, Wallet.dummy(), "mainnet",
                         spot_market_indexes=[0, 6, 8],
                         account_subscription=AccountSubscriptionConfig("cached"))
    await client.subscribe()
    sm = await get_spot_market_account(client.program, 0)  # USDC
    print(vars(sm))
    await client.unsubscribe()
    await conn.close()

asyncio.run(test())