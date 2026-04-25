function AvailBar({ available, total }) {
  const pct = total > 0 ? (available / total) * 100 : 0
  const cls = pct < 10 ? 'empty' : pct < 30 ? 'low' : ''
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div className="inv-bar-wrap" style={{ flex: 1 }}>
        <div className={`inv-bar-fill ${cls}`} style={{ width: `${Math.max(pct, 0)}%` }} />
      </div>
      <span style={{ fontSize: '0.75rem', color: '#94a3b8', minWidth: 48, textAlign: 'right' }}>
        {available.toLocaleString()}
      </span>
    </div>
  )
}

export default function InventoryTable({ inventory }) {
  if (!inventory) return <div className="empty">Loading inventory…</div>
  if (!inventory.length) return <div className="empty">No inventory data.</div>

  return (
    <table>
      <thead>
        <tr>
          <th>Product</th>
          <th>Total</th>
          <th>Reserved</th>
          <th>Available</th>
        </tr>
      </thead>
      <tbody>
        {inventory.map(item => (
          <tr key={item.product_id}>
            <td>{item.name}</td>
            <td>{Number(item.total_qty).toLocaleString()}</td>
            <td>{Number(item.reserved_qty).toLocaleString()}</td>
            <td style={{ minWidth: 160 }}>
              <AvailBar available={Number(item.available)} total={Number(item.total_qty)} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
