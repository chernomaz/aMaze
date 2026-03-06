import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from '@/components/layout/Layout'
import AgentsPage from '@/pages/AgentsPage'
import PoliciesPage from '@/pages/PoliciesPage'
import GraphsPage from '@/pages/GraphsPage'
import SessionsPage from '@/pages/SessionsPage'
import SessionDetailPage from '@/pages/SessionDetailPage'
import RegistryPage from '@/pages/RegistryPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Navigate to="/agents" replace />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="policies" element={<PoliciesPage />} />
            <Route path="graphs" element={<GraphsPage />} />
            <Route path="sessions" element={<SessionsPage />} />
            <Route path="sessions/:id" element={<SessionDetailPage />} />
            <Route path="registry" element={<RegistryPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
