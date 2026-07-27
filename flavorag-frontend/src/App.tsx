import { Outlet } from "react-router-dom";
import ForbiddenToast from "@/components/common/ForbiddenToast";

export default function App() {
  return (
    <>
      <ForbiddenToast />
      <Outlet />
    </>
  );
}
