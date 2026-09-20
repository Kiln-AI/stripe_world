import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import App from "./App"
import { PRODUCT_NAME } from "./brand"
import "./index.css"

document.title = PRODUCT_NAME

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
