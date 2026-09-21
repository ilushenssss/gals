import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"

import "./styles/tokens.css"
import { AppShell } from "./app/AppShell"
import { ToastProvider } from "./shared/ui/Toasts"
import { EnvironmentScreen } from "./features/environment/EnvironmentScreen"
import { FleetScreen } from "./features/fleet/FleetScreen"
import { TaskListScreen } from "./features/task/TaskListScreen"
import { TaskFormScreen } from "./features/task/TaskFormScreen"
import { TaskDetailScreen } from "./features/task/TaskDetailScreen"
import { PlanScreen } from "./features/planning/PlanScreen"
import { SafetyScreen } from "./features/safety/SafetyScreen"
import { ExportScreen } from "./features/export/ExportScreen"

// StrictMode в dev оставлен включённым намеренно: двойной монтаж ловит ошибки
// жизненного цикла Leaflet сразу, а не на проде.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 5_000 },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            {/* Все экраны — дети одного каркаса: карта создаётся один раз и
                переживает переходы между шагами сценария. */}
            <Route element={<AppShell />}>
              <Route path="/" element={<EnvironmentScreen />} />
              <Route path="/environments/:environmentId" element={<EnvironmentScreen />} />
              <Route path="/environments/:environmentId/tasks" element={<TaskListScreen />} />
              <Route path="/environments/:environmentId/tasks/new" element={<TaskFormScreen />} />
              <Route path="/fleet" element={<FleetScreen />} />
              <Route path="/tasks/:taskId" element={<TaskDetailScreen />} />
              <Route path="/tasks/:taskId/edit" element={<TaskFormScreen />} />
              <Route path="/tasks/:taskId/plans" element={<PlanScreen />} />
              <Route path="/tasks/:taskId/plans/:planId" element={<PlanScreen />} />
              <Route path="/tasks/:taskId/plans/:planId/safety" element={<SafetyScreen />} />
              <Route path="/tasks/:taskId/plans/:planId/export" element={<ExportScreen />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
