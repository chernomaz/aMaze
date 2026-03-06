import { NavLink } from 'react-router-dom'
import { Bot, Shield, GitFork, Play, Database, LayoutDashboard } from 'lucide-react'
import { cn } from '@/lib/utils'

const links = [
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/policies', icon: Shield, label: 'Policies' },
  { to: '/graphs', icon: GitFork, label: 'Graphs' },
  { to: '/sessions', icon: Play, label: 'Sessions' },
  { to: '/registry', icon: Database, label: 'Registry' },
]

export default function Sidebar() {
  return (
    <aside className="flex h-screen w-56 flex-col border-r border-border bg-card">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <LayoutDashboard className="h-5 w-5 text-primary" />
        <span className="font-bold tracking-tight text-foreground">aMaze</span>
      </div>
      <nav className="flex-1 space-y-1 p-2 pt-3">
        {links.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-border px-4 py-3">
        <p className="text-xs text-muted-foreground">aMaze v0.1.0</p>
      </div>
    </aside>
  )
}
