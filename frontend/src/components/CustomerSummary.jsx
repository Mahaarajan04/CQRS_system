export default function CustomerSummary({ customers }) {
  if (!customers) return <div className="empty">Loading customers…</div>
  if (!customers.length) return <div className="empty">No customer data yet.</div>

  return (
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Customer</th>
          <th>Orders</th>
          <th>Total Spent</th>
        </tr>
      </thead>
      <tbody>
        {customers.slice(0, 10).map((c, i) => (
          <tr key={c.customer_id}>
            <td style={{ color: '#64748b' }}>{i + 1}</td>
            <td>{c.customer_name}</td>
            <td>{c.total_orders}</td>
            <td>${Number(c.total_spent || 0).toFixed(2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
