import { useEffect, useState } from "react";
import { pageForPath } from "./data";
import { Shell } from "./components/Shell";

export function locationState(location: string) {
  const url = new URL(location, "http://atis.local");
  return { path: url.pathname, fitToWindow: url.searchParams.get("fit") === "window" };
}

export function navigationLocation(next: string, current: string) {
  const url = new URL(next, "http://atis.local");
  if (locationState(current).fitToWindow) url.searchParams.set("fit", "window");
  else url.searchParams.delete("fit");
  return `${url.pathname}${url.search}${url.hash}`;
}

export function App({ initialPath }: { initialPath?: string }) {
  const [location, setLocation] = useState(() => locationState(initialPath ?? window.location.href));
  useEffect(() => {
    if (initialPath) return;
    const update = () => setLocation(locationState(window.location.href));
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, [initialPath]);
  const navigate = (next: string) => {
    if (initialPath) {
      setLocation(current => locationState(navigationLocation(next, `${current.path}${current.fitToWindow ? "?fit=window" : ""}`)));
      return;
    }
    window.history.pushState({}, "", navigationLocation(next, window.location.href));
    setLocation(locationState(window.location.href));
    document.querySelector(".workspace")?.scrollTo({ top: 0 });
  };
  return <Shell page={pageForPath(location.path)} navigate={navigate} fitToWindow={location.fitToWindow} />;
}
