export function reportWebVitals(metric) {
  if (process.env.NODE_ENV === "development" && metric) {
    console.log("Web Vital:", metric);
  }
}