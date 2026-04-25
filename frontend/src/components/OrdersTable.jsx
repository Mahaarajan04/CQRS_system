const STATUS_CLASS = {
  placed:               'badge-placed',
  inventory_reserved:   'badge-reserved',
  inventory_failed:     'badge-failed',
  payment_authorized:   'badge-auth',
  payment_confirmed:    'badge-confirmed',
  payment_failed:       'badge-failed',
  shipped:              'badge-shipped',
  cancelled:            'badge-cancelled',
}

function statusBadge(status) {
  const cls = STATUS_CLASS[status] || 'badge-placed'
  return <span className={`badge ${cls}`}>{status.replace(/_/g, ' ')}</span>
}

function fmt(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function OrdersTable({ orders }) {
  if (!orders) return <div className="empty">Loading orders…</div>
  if (!orders.length) return <div className="empty">No orders yet. Place some via the write API.</div>

  return (
    <div style={{ overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>
            <th>Order ID</th>
            <th>Customer</th>
            <th>Region</th>
            <th>Status</th>
            <th>Total</th>
            <th>Placed At</th>
          </tr>
        </thead>
        <tbody>
          {orders.map(o => (
            <tr key={o.order_id}>
              <td style={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>{o.order_id.slice(0, 8)}…</td>
              <td>{o.customer_name}</td>
              <td>{o.region}</td>
              <td>{statusBadge(o.status)}</td>
              <td>${Number(o.total_amount || 0).toFixed(2)}</td>
              <td>{fmt(o.placed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
