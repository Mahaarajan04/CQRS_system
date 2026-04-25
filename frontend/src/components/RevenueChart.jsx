import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Tooltip,
  Legend,
} from 'chart.js'
import { Bar } from 'react-chartjs-2'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip, Legend)

const REGION_COLORS = {
  'us-east':   '#3b82f6',
  'us-west':   '#8b5cf6',
  'eu-west':   '#10b981',
  'ap-south':  '#f59e0b',
}
const FALLBACK_COLORS = ['#6366f1', '#ec4899', '#14b8a6', '#f97316']

export default function RevenueChart({ revenue }) {
  if (!revenue) return <div className="empty">Loading revenue data…</div>
  if (!revenue.length) return <div className="empty">No revenue data yet. Seed some orders first.</div>

  // Aggregate by region across all dates
  const byRegion = {}
  for (const row of revenue) {
    const r = row.region || 'unknown'
    byRegion[r] = (byRegion[r] || 0) + Number(row.revenue || 0)
  }

  const regions = Object.keys(byRegion)
  const data = {
    labels: regions,
    datasets: [{
      label: 'Revenue ($)',
      data: regions.map(r => byRegion[r].toFixed(2)),
      backgroundColor: regions.map((r, i) => REGION_COLORS[r] || FALLBACK_COLORS[i % FALLBACK_COLORS.length]),
      borderRadius: 4,
    }],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: true,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: ctx => ` $${Number(ctx.raw).toLocaleString(undefined, { minimumFractionDigits: 2 })}`,
        },
      },
    },
    scales: {
      x: { ticks: { color: '#94a3b8' }, grid: { color: '#1e2433' } },
      y: {
        ticks: {
          color: '#94a3b8',
          callback: v => `$${Number(v).toLocaleString()}`,
        },
        grid: { color: '#1e2433' },
      },
    },
  }

  return <Bar data={data} options={options} />
}
