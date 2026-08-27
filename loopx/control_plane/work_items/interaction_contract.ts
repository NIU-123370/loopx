import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
  requireStringLiteral,
} from "../runtime_decode.ts";

import type { JsonObject } from "../effect_program.ts";

export const INTERACTION_CONTRACT_SCHEMA_VERSION =
  "loopx_interaction_contract_v0";

const USER_CHANNEL_NOTIFICATION_POLICIES = ["NOTIFY", "DONT_NOTIFY"] as const;

export interface UserInteractionChannel extends JsonObject {
  action_required: boolean;
  notify: (typeof USER_CHANNEL_NOTIFICATION_POLICIES)[number];
  non_blocking?: true;
  actions?: string[];
}

export type AgentInteractionChannel =
  | (JsonObject & {
      must_attempt: true;
      delivery_allowed: boolean;
      quiet_noop_allowed: false;
    })
  | (JsonObject & {
      must_attempt: false;
      delivery_allowed: false;
      quiet_noop_allowed: boolean;
    });

export interface InteractionContract extends JsonObject {
  schema_version: typeof INTERACTION_CONTRACT_SCHEMA_VERSION;
  mode: string;
  user_channel: UserInteractionChannel;
  agent_channel: AgentInteractionChannel;
  cli_channel: JsonObject;
}

function decodeUserChannel(value: unknown): UserInteractionChannel {
  const channel = requireJsonObject(value, "interaction_contract.user_channel");
  const actionRequired = requireBoolean(
    channel.action_required,
    "interaction_contract.user_channel.action_required",
  );
  const notify = requireStringLiteral(
    channel.notify,
    USER_CHANNEL_NOTIFICATION_POLICIES,
    "interaction_contract.user_channel.notify",
  );
  const nonBlocking = channel.non_blocking === undefined
    ? undefined
    : requireBoolean(
      channel.non_blocking,
      "interaction_contract.user_channel.non_blocking",
    );
  if (actionRequired && nonBlocking === true) {
    throw new EffectRuntimeRequestError(
      "interaction_contract.user_channel cannot be both required and non-blocking",
    );
  }
  if (nonBlocking === false) {
    throw new EffectRuntimeRequestError(
      "interaction_contract.user_channel.non_blocking must be true when present",
    );
  }
  const decoded: UserInteractionChannel = {
    ...channel,
    action_required: actionRequired,
    notify,
  };
  if (nonBlocking === true) decoded.non_blocking = true;
  if (channel.actions !== undefined) {
    decoded.actions = requireStringArray(
      channel.actions,
      "interaction_contract.user_channel.actions",
    );
  }
  return decoded;
}

function decodeAgentChannel(
  value: unknown,
  { userActionRequired }: { userActionRequired: boolean },
): AgentInteractionChannel {
  const channel = requireJsonObject(value, "interaction_contract.agent_channel");
  const mustAttempt = requireBoolean(
    channel.must_attempt,
    "interaction_contract.agent_channel.must_attempt",
  );
  const deliveryAllowed = requireBoolean(
    channel.delivery_allowed,
    "interaction_contract.agent_channel.delivery_allowed",
  );
  const quietNoopAllowed = requireBoolean(
    channel.quiet_noop_allowed,
    "interaction_contract.agent_channel.quiet_noop_allowed",
  );
  if (deliveryAllowed && !mustAttempt) {
    throw new EffectRuntimeRequestError(
      "interaction_contract.agent_channel cannot allow delivery without an attempt",
    );
  }
  if (quietNoopAllowed && (mustAttempt || deliveryAllowed || userActionRequired)) {
    throw new EffectRuntimeRequestError(
      "interaction_contract quiet no-op conflicts with a required action",
    );
  }
  if (mustAttempt) {
    return {
      ...channel,
      must_attempt: true,
      delivery_allowed: deliveryAllowed,
      quiet_noop_allowed: false,
    };
  }
  return {
    ...channel,
    must_attempt: false,
    delivery_allowed: false,
    quiet_noop_allowed: quietNoopAllowed,
  };
}

/** Decode the final host-facing interaction decision at the existing Effect boundary. */
export function decodeInteractionContract(value: unknown): InteractionContract {
  const contract = requireJsonObject(value, "interaction_contract");
  if (contract.schema_version !== INTERACTION_CONTRACT_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `interaction_contract.schema_version must be ${INTERACTION_CONTRACT_SCHEMA_VERSION}`,
    );
  }
  const userChannel = decodeUserChannel(contract.user_channel);
  const agentChannel = decodeAgentChannel(
    contract.agent_channel,
    { userActionRequired: userChannel.action_required },
  );
  return {
    ...contract,
    schema_version: INTERACTION_CONTRACT_SCHEMA_VERSION,
    mode: requireNonEmptyString(contract.mode, "interaction_contract.mode"),
    user_channel: userChannel,
    agent_channel: agentChannel,
    cli_channel: requireJsonObject(
      contract.cli_channel,
      "interaction_contract.cli_channel",
    ),
  };
}
