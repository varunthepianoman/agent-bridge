import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { BioEditor, conversationCardSummary } from "./App";
import type { CoreConversation } from "./types";

afterEach(() => vi.unstubAllGlobals());

const conversation: CoreConversation = {
  conversation_id: "conversation-1",
  display_name: "Chat 1 · Builder",
  alias: "Builder",
  bio: "Build and deployment specialist",
  provider: "codex",
  provider_thread_id: "thread-1",
  preview: "Old transcript preview",
  transcript_text: "assistant: ready",
  status: "idle",
  node_id: "hub",
  environment_id: "host",
  cwd: "/work",
  conversation_kind: "full",
  delivery_mode: "direct",
  selected: true,
  native_launch_enabled: true,
  capabilities: { can_open: true, can_receive_turn: true, can_message: true },
};

it("prefers bios on cards and edits the detail bio", async () => {
  const requests: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push([url, init]);
    const body = { ...conversation, bio: JSON.parse(String(init?.body)).bio };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><BioEditor conversation={conversation} /></QueryClientProvider>);

  expect(conversationCardSummary(conversation)).toBe("Build and deployment specialist");
  expect(conversationCardSummary({ ...conversation, bio: "" })).toBe("Old transcript preview");
  const editor = screen.getByRole("textbox", { name: /Public bio/ });
  fireEvent.change(editor, { target: { value: "Release specialist" } });
  fireEvent.click(screen.getByText("Save bio"));

  await waitFor(() => expect(requests.some(([url, init]) =>
    url.endsWith("/conversations/conversation-1") && init?.method === "PATCH" &&
    JSON.parse(String(init.body)).bio === "Release specialist",
  )).toBe(true));
});
