import path from "path";
import { fileURLToPath } from "url";
import express from "express";
import { safetyScoreHandler } from "./safetyScoreRoute.js";

const app = express();
app.use(express.json());

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

app.use(express.static(path.join(__dirname, "client")));

app.get("/", (_, res) => {
  res.sendFile(path.join(__dirname, "client", "index.html"));
});

app.post("/api/safetyScore", safetyScoreHandler);

export default app;
