import * as http from "http";
import * as vscode from "vscode";

const PORT = 7823;
// Only accept requests from Chrome extensions and localhost
const ALLOWED_ORIGINS = /^chrome-extension:\/\/|^http:\/\/localhost/;

export class TokenServer {
  private _server?: http.Server;
  private _onToken: (token: string) => void;

  constructor(onToken: (token: string) => void) {
    this._onToken = onToken;
  }

  start(secrets: vscode.SecretStorage): void {
    this._server = http.createServer(async (req, res) => {
      res.setHeader("Access-Control-Allow-Origin", "*");
      res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
      res.setHeader("Access-Control-Allow-Headers", "Content-Type");

      if (req.method === "OPTIONS") {
        res.writeHead(204);
        res.end();
        return;
      }

      const origin = req.headers["origin"] ?? "";
      if (!ALLOWED_ORIGINS.test(origin) && !req.headers["x-costtrack-local"]) {
        res.writeHead(403, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Forbidden" }));
        return;
      }

      if (req.method !== "POST" || req.url !== "/token") {
        res.writeHead(404);
        res.end();
        return;
      }

      let body = "";
      req.on("data", (chunk: Buffer) => { body += chunk.toString(); });
      req.on("end", async () => {
        try {
          const { token } = JSON.parse(body) as { token?: string };
          if (!token || typeof token !== "string" || token.length < 10) {
            res.writeHead(400, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Invalid token" }));
            return;
          }

          await secrets.store("cursor-session-token", token);
          this._onToken(token);

          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: true, email: "" }));
        } catch {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Bad request" }));
        }
      });
    });

    this._server.listen(PORT, "127.0.0.1", () => {
      console.log(`[Cursor Cost Tracker] Token server listening on localhost:${PORT}`);
    });

    this._server.on("error", (err: NodeJS.ErrnoException) => {
      if (err.code === "EADDRINUSE") {
        // Port already in use — another instance is running, that's fine
        console.log(`[Cursor Cost Tracker] Port ${PORT} already in use, skipping server start`);
      }
    });
  }

  stop(): void {
    this._server?.close();
  }
}
