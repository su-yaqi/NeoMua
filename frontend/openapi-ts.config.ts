import { defineConfig } from "@hey-api/openapi-ts"

export default defineConfig({
  input: "./openapi.json",
  output: "./src/client",

  plugins: [
    "@hey-api/client-axios",
    "@hey-api/typescript",
    {
      name: "@hey-api/sdk",
      paramsStructure: "flat",
    },
    {
      name: "@hey-api/schemas",
      type: "json",
    },
  ],
})
