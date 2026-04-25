export default function CacheStats({ stats }) {
  if (!stats) return <div className="cache-stats"><div className="empty">Loading cache stats…</div></div>

  const hitRate = stats.total > 0 ? (stats.hit_rate * 100).toFixed(1) : '0.0'

  return (
    <div className="cache-stats">
      <div className="stat-chip hit-rate">
        <span className="stat-label">Hit Rate</span>
        <span className="stat-value">{hitRate}%</span>
      </div>
      <div className="stat-chip">
        <span className="stat-label">Hits</span>
        <span className="stat-value">{stats.hits.toLocaleString()}</span>
      </div>
      <div className="stat-chip">
        <span className="stat-label">Misses</span>
        <span className="stat-value">{stats.misses.toLocaleString()}</span>
      </div>
      <div className="stat-chip">
        <span className="stat-label">Total Reads</span>
        <span className="stat-value">{stats.total.toLocaleString()}</span>
      </div>
    </div>
  )
}
