from ib_async import IB, Stock, MarketOrder

ib = IB()
ib.connect('127.0.0.1', 4002, clientId=1)

print("Connected:", ib.isConnected())
for row in ib.accountSummary():
    if row.tag in ('NetLiquidation', 'TotalCashValue', 'BuyingPower'):
        print(row.tag, row.value, row.currency)

print("Positions:", ib.positions())

contract = Stock('AAPL', 'SMART', 'USD')
ib.qualifyContracts(contract)
trade = ib.placeOrder(contract, MarketOrder('BUY', 1))
ib.sleep(3)
print("Order status:", trade.orderStatus.status)

ib.disconnect()
