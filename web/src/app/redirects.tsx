/** Прежний адрес «задачи обстановки» ведет в историю с фильтром по ней. */
import { Navigate, useParams } from "react-router-dom"

export function EnvironmentTasksRedirect() {
  const { environmentId } = useParams()
  return <Navigate to={`/catalog/history?env=${environmentId}`} replace />
}
