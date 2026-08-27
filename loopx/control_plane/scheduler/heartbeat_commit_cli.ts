import {
  effectRuntimeErrorPayload,
  EffectRuntimeRequestError,
} from "../effect_runtime_errors.ts";
import {
  evaluateSchedulerHeartbeatHostFacts,
  SCHEDULER_HEARTBEAT_COMMIT_ERROR_SCHEMA,
  type SchedulerHeartbeatCommitError,
} from "./heartbeat_commit.ts";

function errorEnvelope(error: unknown): SchedulerHeartbeatCommitError {
  return {
    schema_version: SCHEDULER_HEARTBEAT_COMMIT_ERROR_SCHEMA,
    status: "error",
    error: effectRuntimeErrorPayload(error),
  };
}

async function readRequest(): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(typeof chunk === "string" ? Buffer.from(chunk) : chunk);
  }
  const input = Buffer.concat(chunks).toString("utf8");
  if (!input.trim()) {
    throw new EffectRuntimeRequestError(
      "scheduler heartbeat host facts input must not be empty",
      "empty_request",
    );
  }
  try {
    return JSON.parse(input);
  } catch {
    throw new EffectRuntimeRequestError(
      "scheduler heartbeat host facts input must be valid JSON",
      "invalid_json",
    );
  }
}

async function main(): Promise<number> {
  try {
    const result = await evaluateSchedulerHeartbeatHostFacts(await readRequest());
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return result.status === "conflict" ? 1 : 0;
  } catch (error) {
    process.stdout.write(`${JSON.stringify(errorEnvelope(error))}\n`);
    return 1;
  }
}

const exitCode = await main();
process.exitCode = exitCode;
