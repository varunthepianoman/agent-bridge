import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Archive,
  Bot,
  BriefcaseBusiness,
  CircleDot,
  EyeOff,
  Inbox,
  Network,
  PanelLeftClose,
  Pin,
  RadioTower,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";
import {
  approveConvergenceImplementation,
  approveConvergencePublish,
  attachConversation,
  createRelationship,
  createRole,
  createWorkItem,
  deleteRelationship,
  detachConversation,
  getConversation,
  getWorkItem,
  listConversations,
  listNodes,
  listRelationships,
  listRoles,
  listWorkItems,
  resumeConversation,
  syncCatalog,
  updateConversation,
  updateWorkItem,
} from "./api";
import { ConversationDetail } from "./components/ConversationDetail";
import { ConversationList } from "./components/ConversationList";
import { CoordinatorCenter } from "./components/CoordinatorCenter";
import { NodeOverview } from "./components/NodeOverview";
import { ManualBridge } from "./components/ManualBridge";
import { WorkDetail } from "./components/WorkDetail";
import { WorkPortfolio } from "./components/WorkPortfolio";
import { OperationsCenter } from "./components/OperationsCenter";
import type {
  ConversationFilters,
  ConversationPatch,
  RelationshipInput,
  RoleInput,
  WorkItemInput,
} from "./types";

const initialFilters: ConversationFilters = {
  query: "",
  status: "all",
  source: "all",
  view: "all",
};

export function App() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState(initialFilters);
  const [queryDraft, setQueryDraft] = useState("");
  const [selectedId, setSelectedId] = useState<string>();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [toast, setToast] = useState<string>();
  const [section, setSection] = useState<
    "work" | "conversations" | "nodes" | "manual" | "coordinator" | "operations"
  >("work");
  const [selectedWorkId, setSelectedWorkId] = useState<string>();
  const [coordinatorTarget, setCoordinatorTarget] = useState<{
    workId?: string;
    roleId?: string;
  }>({});

  useEffect(() => {
    const timer = window.setTimeout(
      () => setFilters((current) => ({ ...current, query: queryDraft })),
      220,
    );
    return () => window.clearTimeout(timer);
  }, [queryDraft]);

  const listQuery = useQuery({
    queryKey: ["conversations", filters],
    queryFn: () => listConversations(filters),
  });
  const conversations = listQuery.data?.items ?? [];
  useEffect(() => {
    if (!selectedId && conversations.length) setSelectedId(conversations[0].id);
  }, [conversations, selectedId]);

  const detailQuery = useQuery({
    queryKey: ["conversation", selectedId],
    queryFn: () => getConversation(selectedId!),
    enabled: Boolean(selectedId),
  });
  const workListQuery = useQuery({
    queryKey: ["work-items"],
    queryFn: listWorkItems,
    refetchInterval: section === "work" ? 5_000 : false,
  });
  const nodesQuery = useQuery({
    queryKey: ["nodes"],
    queryFn: listNodes,
    enabled: section === "nodes",
  });
  const workItems = workListQuery.data ?? [];
  useEffect(() => {
    if (!selectedWorkId && workItems.length) setSelectedWorkId(workItems[0].id);
    if (
      selectedWorkId &&
      workItems.length &&
      !workItems.some((item) => item.id === selectedWorkId)
    )
      setSelectedWorkId(workItems[0].id);
  }, [selectedWorkId, workItems]);
  const workDetailQuery = useQuery({
    queryKey: ["work-item", selectedWorkId],
    queryFn: () => getWorkItem(selectedWorkId!),
    enabled: Boolean(selectedWorkId),
    refetchInterval: section === "work" ? 5_000 : false,
  });
  const rolesQuery = useQuery({
    queryKey: ["roles", selectedWorkId],
    queryFn: () => listRoles(selectedWorkId),
    enabled: Boolean(selectedWorkId),
    refetchInterval: section === "work" ? 5_000 : false,
  });
  const relationshipsQuery = useQuery({
    queryKey: ["relationships", selectedWorkId],
    queryFn: () => listRelationships(selectedWorkId),
    enabled: Boolean(selectedWorkId),
  });
  const sources = useMemo(
    () =>
      [
        ...new Set(
          conversations.map((item) => item.source_kind).filter(Boolean),
        ),
      ] as string[],
    [conversations],
  );

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(undefined), 2600);
  };
  const syncMutation = useMutation({
    mutationFn: syncCatalog,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      notify("Catalog sync completed");
    },
    onError: (error: Error) => notify(error.message),
  });
  const updateMutation = useMutation({
    mutationFn: (patch: ConversationPatch) =>
      updateConversation(selectedId!, patch),
    onSuccess: (conversation) => {
      queryClient.setQueryData(["conversation", selectedId], conversation);
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      notify("Conversation updated");
    },
    onError: (error: Error) => notify(error.message),
  });
  const resumeMutation = useMutation({
    mutationFn: () => resumeConversation(selectedId!),
    onSuccess: (result) =>
      notify(result.message || "Opening conversation on its native node"),
    onError: (error: Error) => notify(error.message),
  });
  const refreshWork = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["work-items"] }),
      queryClient.invalidateQueries({
        queryKey: ["work-item", selectedWorkId],
      }),
      queryClient.invalidateQueries({ queryKey: ["roles", selectedWorkId] }),
      queryClient.invalidateQueries({
        queryKey: ["relationships", selectedWorkId],
      }),
    ]);
  };
  const createWorkMutation = useMutation({
    mutationFn: createWorkItem,
    onSuccess: async (work) => {
      setSelectedWorkId(work.id);
      await refreshWork();
      notify("Work item created");
    },
    onError: (error: Error) => notify(error.message),
  });
  const updateWorkMutation = useMutation({
    mutationFn: (input: Partial<WorkItemInput>) =>
      updateWorkItem(selectedWorkId!, input),
    onSuccess: async () => {
      await refreshWork();
      notify("Work item updated");
    },
    onError: (error: Error) => notify(error.message),
  });
  const attachMutation = useMutation({
    mutationFn: (id: string) => attachConversation(selectedWorkId!, id),
    onSuccess: async () => {
      await refreshWork();
      notify("Conversation attached");
    },
    onError: (error: Error) => notify(error.message),
  });
  const detachMutation = useMutation({
    mutationFn: (id: string) => detachConversation(selectedWorkId!, id),
    onSuccess: async () => {
      await refreshWork();
      notify("Conversation removed");
    },
    onError: (error: Error) => notify(error.message),
  });
  const roleMutation = useMutation({
    mutationFn: (input: RoleInput) => createRole(input),
    onSuccess: async () => {
      await refreshWork();
      notify("Durable role created");
    },
    onError: (error: Error) => notify(error.message),
  });
  const relationshipMutation = useMutation({
    mutationFn: (input: RelationshipInput) => createRelationship(input),
    onSuccess: async () => {
      await refreshWork();
      notify("Relationship created");
    },
    onError: (error: Error) => notify(error.message),
  });
  const deleteRelationshipMutation = useMutation({
    mutationFn: deleteRelationship,
    onSuccess: async () => {
      await refreshWork();
      notify("Relationship removed");
    },
    onError: (error: Error) => notify(error.message),
  });
  const approvePublishMutation = useMutation({
    mutationFn: () => approveConvergencePublish(selectedWorkId!),
    onSuccess: async () => {
      await refreshWork();
      notify("Push and post approved");
    },
    onError: (error: Error) => notify(error.message),
  });
  const approveImplementationMutation = useMutation({
    mutationFn: () => approveConvergenceImplementation(selectedWorkId!),
    onSuccess: async () => {
      await refreshWork();
      notify("Implementation approved and queued");
    },
    onError: (error: Error) => notify(error.message),
  });

  return (
    <main className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "" : "collapsed"}`}>
        <div className="brand">
          <span className="brand-mark">
            <Sparkles size={18} />
          </span>
          <div>
            <strong>Agent Bridge</strong>
            <span>Work Catalog</span>
          </div>
        </div>
        <nav aria-label="Catalog views">
          <NavItem
            icon={<BriefcaseBusiness size={16} />}
            label="Focused work"
            active={section === "work"}
            count={workItems.length}
            onClick={() => setSection("work")}
          />
          <NavItem
            icon={<Activity size={16} />}
            label="Operations"
            active={section === "operations"}
            onClick={() => setSection("operations")}
          />
          <NavItem
            icon={<Bot size={16} />}
            label="Coordinator"
            active={section === "coordinator"}
            onClick={() => {
              setCoordinatorTarget({});
              setSection("coordinator");
            }}
          />
          <NavItem
            icon={<Network size={16} />}
            label="Nodes"
            active={section === "nodes"}
            count={nodesQuery.data?.length}
            onClick={() => setSection("nodes")}
          />
          <NavItem
            icon={<RadioTower size={16} />}
            label="Manual Bridge"
            active={section === "manual"}
            onClick={() => setSection("manual")}
          />
          <div className="nav-separator" />
          <NavItem
            icon={<Inbox size={16} />}
            label="All conversations"
            active={section === "conversations" && filters.view === "all"}
            count={listQuery.data?.total}
            onClick={() => {
              setSection("conversations");
              setFilters((value) => ({ ...value, view: "all" }));
            }}
          />
          <NavItem
            icon={<Pin size={16} />}
            label="Pinned"
            active={section === "conversations" && filters.view === "pinned"}
            onClick={() => {
              setSection("conversations");
              setFilters((value) => ({ ...value, view: "pinned" }));
            }}
          />
          <NavItem
            icon={<Archive size={16} />}
            label="Archived"
            active={section === "conversations" && filters.view === "archived"}
            onClick={() => {
              setSection("conversations");
              setFilters((value) => ({ ...value, view: "archived" }));
            }}
          />
          <NavItem
            icon={<EyeOff size={16} />}
            label="Hidden"
            active={section === "conversations" && filters.view === "hidden"}
            onClick={() => {
              setSection("conversations");
              setFilters((value) => ({ ...value, view: "hidden" }));
            }}
          />
        </nav>
        <div className="sidebar-footer">
          <span className="online-dot" />
          Local catalog connected
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <button
            className="icon-button sidebar-toggle"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            title="Toggle navigation"
          >
            <PanelLeftClose size={17} />
          </button>
          <div className="search-box">
            <Search size={17} />
            <input
              aria-label="Search conversations"
              value={queryDraft}
              onFocus={() => setSection("conversations")}
              onChange={(event) => setQueryDraft(event.target.value)}
              placeholder="Search every Codex conversation…"
            />
            <kbd>⌘ K</kbd>
          </div>
          <button
            className="secondary-button"
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending}
          >
            <RefreshCw
              size={15}
              className={syncMutation.isPending ? "spinning" : ""}
            />
            Sync
          </button>
        </header>
        {section === "operations" ? (
          <OperationsCenter />
        ) : section === "coordinator" ? (
          <CoordinatorCenter
            key={`${coordinatorTarget.workId || "portfolio"}:${coordinatorTarget.roleId || ""}`}
            workItems={workItems}
            initialWorkId={coordinatorTarget.workId}
            initialRoleId={coordinatorTarget.roleId}
            onOpenManual={() => setSection("manual")}
          />
        ) : section === "manual" ? (
          <ManualBridge />
        ) : section === "nodes" ? (
          <NodeOverview
            nodes={nodesQuery.data ?? []}
            loading={nodesQuery.isLoading}
            error={nodesQuery.isError ? nodesQuery.error.message : undefined}
            onRetry={() => void nodesQuery.refetch()}
          />
        ) : section === "conversations" ? (
          <div className="catalog-layout">
            <section className="list-panel">
              <div className="list-heading">
                <div>
                  <h1>{viewTitle(filters.view)}</h1>
                  <span>
                    {listQuery.data?.total ?? 0} conversations across your work
                  </span>
                </div>
              </div>
              <div className="filter-row">
                <label>
                  <span>Status</span>
                  <select
                    value={filters.status}
                    onChange={(event) =>
                      setFilters((value) => ({
                        ...value,
                        status: event.target.value,
                      }))
                    }
                  >
                    <option value="all">Any status</option>
                    <option value="active">Active</option>
                    <option value="idle">Idle</option>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                  </select>
                </label>
                <label>
                  <span>Source</span>
                  <select
                    value={filters.source}
                    onChange={(event) =>
                      setFilters((value) => ({
                        ...value,
                        source: event.target.value,
                      }))
                    }
                  >
                    <option value="all">All sources</option>
                    <option value="vscode">VS Code</option>
                    <option value="cli">CLI</option>
                    <option value="appServer">App Server</option>
                    {sources
                      .filter(
                        (source) =>
                          !["vscode", "cli", "appServer"].includes(source),
                      )
                      .map((source) => (
                        <option value={source} key={source}>
                          {source}
                        </option>
                      ))}
                  </select>
                </label>
              </div>
              {listQuery.isError ? (
                <div className="error-banner">
                  <CircleDot size={16} />
                  {listQuery.error.message}
                  <button onClick={() => listQuery.refetch()}>Retry</button>
                </div>
              ) : (
                <ConversationList
                  conversations={conversations}
                  selectedId={selectedId}
                  loading={listQuery.isLoading}
                  onSelect={setSelectedId}
                />
              )}
            </section>
            <ConversationDetail
              conversation={detailQuery.data}
              loading={detailQuery.isLoading}
              saving={updateMutation.isPending}
              resuming={resumeMutation.isPending}
              onUpdate={(patch) => updateMutation.mutate(patch)}
              onResume={() => resumeMutation.mutate()}
            />
          </div>
        ) : (
          <div className="catalog-layout work-layout">
            <WorkPortfolio
              items={workItems}
              selectedId={selectedWorkId}
              loading={workListQuery.isLoading}
              creating={createWorkMutation.isPending}
              onSelect={setSelectedWorkId}
              onCreate={(input) => createWorkMutation.mutate(input)}
            />
            <WorkDetail
              work={workDetailQuery.data}
              roles={rolesQuery.data ?? []}
              relationships={relationshipsQuery.data ?? []}
              allConversations={conversations}
              loading={workDetailQuery.isLoading}
              saving={updateWorkMutation.isPending}
              approvingImplementation={approveImplementationMutation.isPending}
              onApproveImplementation={() =>
                approveImplementationMutation.mutate()
              }
              approvingPublish={approvePublishMutation.isPending}
              onApprovePublish={() => approvePublishMutation.mutate()}
              onUpdate={(input) => updateWorkMutation.mutate(input)}
              onAttach={(id) => attachMutation.mutate(id)}
              onDetach={(id) => detachMutation.mutate(id)}
              onCreateRole={(input) => roleMutation.mutate(input)}
              onCreateRelationship={(input) =>
                relationshipMutation.mutate(input)
              }
              onDeleteRelationship={(id) =>
                deleteRelationshipMutation.mutate(id)
              }
              onOpenConversation={(id) => {
                setSelectedId(id);
                setSection("conversations");
              }}
              onCoordinate={(workId, roleId) => {
                setCoordinatorTarget({ workId, roleId });
                setSection("coordinator");
              }}
            />
          </div>
        )}
      </section>
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </main>
  );
}

function NavItem({
  icon,
  label,
  active,
  count,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  count?: number;
  onClick: () => void;
}) {
  return (
    <button className={`nav-item ${active ? "active" : ""}`} onClick={onClick}>
      {icon}
      <span>{label}</span>
      {count !== undefined && <em>{count}</em>}
    </button>
  );
}

function viewTitle(view: ConversationFilters["view"]) {
  return {
    all: "Conversations",
    pinned: "Pinned conversations",
    archived: "Archive",
    hidden: "Hidden conversations",
  }[view];
}
