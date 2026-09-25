import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"

import "./styles/tokens.css"
import "./styles/ui.css"
import { ThemeProvider } from "./app/theme"
import { AppShell } from "./app/AppShell"
import { ToastProvider } from "./shared/ui/Toasts"
import { FleetCatalogScreen } from "./features/catalog/FleetCatalogScreen"
import { HistoryScreen } from "./features/catalog/HistoryScreen"
import { NewTaskScreen } from "./features/catalog/NewTaskScreen"
import { EnvironmentScreen, TaskEnvironmentScreen } from "./features/environment/EnvironmentScreen"
import { TaskFormScreen } from "./features/task/TaskFormScreen"
import { TaskDetailScreen } from "./features/task/TaskDetailScreen"
import { PlanScreen } from "./features/planning/PlanScreen"
import { SafetyScreen } from "./features/safety/SafetyScreen"
import { ExportScreen } from "./features/export/ExportScreen"
import { EnvironmentTasksRedirect } from "./app/redirects"

// StrictMode в dev оставлен включённым намеренно: двойной монтаж ловит ошибки
// жизненного цикла Leaflet сразу, а не на проде.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 5_000 },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            {/* Все экраны — дети одного каркаса: карта создаётся один раз и
                переживает переходы между шагами сценария. */}
            <Route element={<AppShell />}>
              <Route path="/" element={<Navigate to="/catalog/fleet" replace />} />
              {/* Каталог: вход до выбора задачи */}
              <Route path="/catalog/fleet" element={<FleetCatalogScreen />} />
              <Route path="/catalog/fleet/:fleetId" element={<FleetCatalogScreen />} />
              <Route path="/catalog/fleet/:fleetId/:inventory" element={<FleetCatalogScreen />} />
              <Route path="/catalog/history" element={<HistoryScreen />} />
              <Route path="/catalog/new" element={<NewTaskScreen />} />
              {/* Новая задача: обстановка → форма */}
              <Route path="/environments/:environmentId" element={<EnvironmentScreen />} />
              <Route path="/environments/:environmentId/tasks" element={<EnvironmentTasksRedirect />} />
              <Route path="/environments/:environmentId/tasks/new" element={<TaskFormScreen />} />
              {/* Сценарий задачи: этапы 1–5 */}
              <Route path="/tasks/:taskId" element={<TaskDetailScreen />} />
              <Route path="/tasks/:taskId/environment" element={<TaskEnvironmentScreen />} />
              <Route path="/tasks/:taskId/edit" element={<TaskFormScreen />} />
              <Route path="/tasks/:taskId/plans" element={<PlanScreen />} />
              <Route path="/tasks/:taskId/plans/:planId" element={<PlanScreen />} />
              <Route path="/tasks/:taskId/plans/:planId/safety" element={<SafetyScreen />} />
              <Route path="/tasks/:taskId/plans/:planId/export" element={<ExportScreen />} />
              {/* прежние адреса */}
              <Route path="/fleet" element={<Navigate to="/catalog/fleet" replace />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
