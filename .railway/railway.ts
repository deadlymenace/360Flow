import { defineRailway, project, service } from "railway/iac";

/**
 * Railway does not read this file on deploy. Apply it with:
 *   railway config plan
 *   railway config apply
 *
 * The image is still the root Dockerfile. Review the plan before apply:
 * a service name that does not match the existing service can delete resources.
 * Restart policy is not part of this DSL; set it in the dashboard if you need it.
 */
export default defineRailway(() => {
  const api = service("360flow", {
    healthcheck: "/health",
    healthcheckTimeout: 60,
  });
  return project("360flow", { resources: [api] });
});
