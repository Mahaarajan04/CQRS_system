import { useState, useEffect } from 'react'
import CacheStats from './components/CacheStats'
import OrdersTable from './components/OrdersTable'
import InventoryTable from './components/InventoryTable'
import RevenueChart from './components/RevenueChart'
import CustomerSummary from './components/CustomerSummary'
import './App.css'

const READ_API = ''

function usePoll(url, interval = 3000) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const fetch_data = () =>
      fetch(url)
        .then(r => r.json())
        .then(d => { if (!cancelled) setData(d) })
        .catch(e => { if (!cancelled) setError(e.message) })

    fetch_data()
    const id = setInterval(fetch_data, interval)
    return () => { cancelled = true; clearInterval(id) }
  }, [url, interval])

  return { data, error }
}

export { READ_API, usePoll }

export default function App() {
  const { data: cacheStats } = usePoll(`${READ_API}/cache/stats`, 1000)
  const { data: orders }     = usePoll(`${READ_API}/orders?limit=20`, 1000)
  const { data: inventory }  = usePoll(`${READ_API}/inventory`, 1000)
  const { data: revenue }    = usePoll(`${READ_API}/analytics/revenue`, 3000)
  const { data: customers }  = usePoll(`${READ_API}/customers/summary`, 3000)

  return (
    <div className="app">
      <header className="app-header">
        <h1>CQRS Live Dashboard</h1>
        <span className="subtitle">Read-your-writes cache · Materialized views · Event sourcing</span>
      </header>

      <div className="top-bar">
        <CacheStats stats={cacheStats} />
      </div>

      <div className="grid">
        <div className="card wide">
          <h2>Recent Orders</h2>
          <OrdersTable orders={orders} />
        </div>

        <div className="card">
          <h2>Inventory</h2>
          <InventoryTable inventory={inventory} />
        </div>

        <div className="card">
          <h2>Revenue by Region</h2>
          <RevenueChart revenue={revenue} />
        </div>

        <div className="card">
          <h2>Top Customers</h2>
          <CustomerSummary customers={customers} />
        </div>
      </div>
    </div>
  )
}
