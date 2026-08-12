import {
  Bot,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Link2,
  ListTree,
  MessageSquareText,
  Network,
  Plus,
  RadioTower,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type {
  Conversation,
  CoordinatorRole,
  RelationshipInput,
  RoleInput,
  WorkItemDetail,
  WorkItemInput,
  WorkRelationship,
} from "../types";
import { WorkGraph } from "./WorkGraph";
import { ManualBridge } from "./ManualBridge";

interface Props {
  work?: WorkItemDetail;
  roles: CoordinatorRole[];
  relationships: WorkRelationship[];
  allConversations: Conversation[];
  loading: boolean;
  saving: boolean;
  onUpdate: (input: Partial<WorkItemInput>) => void;
  onAttach: (conversationId: string) => void;
  onDetach: (conversationId: string) => void;
  onCreateRole: (input: RoleInput) => void;
  onCreateRelationship: (input: RelationshipInput) => void;
  onDeleteRelationship: (id: string) => void;
  onOpenConversation: (id: string) => void;
  onCoordinate?: (workId: string, roleId?: string) => void;
}

export function WorkDetail(props: Props) {
  const { work, roles, relationships, allConversations, loading } = props;
  const [tab, setTab] = useState<"overview" | "graph" | "manual">("overview");
  const [editing, setEditing] = useState(false);
  const [objective, setObjective] = useState("");
  const [attachId, setAttachId] = useState("");
  const [roleType, setRoleType] =
    useState<RoleInput["role_type"]>("work_coordinator");
  const [charter, setCharter] = useState("");
  const [parentRoleId, setParentRoleId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [relationshipKind, setRelationshipKind] = useState("coordinates");

  useEffect(() => {
    setObjective(work?.objective || "");
    setEditing(false);
  }, [work?.id, work?.objective]);
  const unattached = useMemo(
    () =>
      allConversations.filter(
        (conversation) =>
          !work?.conversations.some((item) => item.id === conversation.id),
      ),
    [allConversations, work],
  );
  const entityOptions = useMemo(
    () =>
      work
        ? [
            {
              id: work.id,
              label: `Work: ${work.title}`,
              type: "work" as const,
            },
            ...roles.map((role) => ({
              id: role.id,
              label: `Role: ${role.charter}`,
              type: "role" as const,
            })),
            ...work.conversations.map((conversation) => ({
              id: conversation.id,
              label: `Chat: ${conversation.catalog_title || conversation.title || conversation.id}`,
              type: "conversation" as const,
            })),
          ]
        : [],
    [roles, work],
  );
  const scopedRelationships = useMemo(() => {
    const ids = new Set(entityOptions.map((item) => item.id));
    return relationships.filter(
      (item) => ids.has(item.source_id) && ids.has(item.target_id),
    );
  }, [entityOptions, relationships]);

  if (loading)
    return (
      <section className="detail-panel">
        <div className="list-state">
          <span className="spinner" />
          Loading focused work…
        </div>
      </section>
    );
  if (!work)
    return (
      <section className="detail-panel detail-welcome">
        <span className="welcome-orbit">
          <ListTree size={28} />
        </span>
        <h2>One task at a time.</h2>
        <p>
          Select a work item to see its conversations, durable coordinator
          roles, and relationships.
        </p>
      </section>
    );

  const submitRole = () => {
    if (!charter.trim()) return;
    props.onCreateRole({
      work_item_id: work.id,
      role_type: roleType,
      charter: charter.trim(),
      parent_role_id: parentRoleId || undefined,
      authority_profile: "delegate-bounded",
      status: "planned",
    });
    setCharter("");
    setParentRoleId("");
  };
  const submitRelationship = () => {
    const source = entityOptions.find((item) => item.id === sourceId);
    const target = entityOptions.find((item) => item.id === targetId);
    if (!source || !target || source.id === target.id) return;
    props.onCreateRelationship({
      work_item_id: work.id,
      source_type: source.type,
      source_id: source.id,
      target_type: target.type,
      target_id: target.id,
      kind: relationshipKind,
    });
  };
  const entityName = (id: string) =>
    entityOptions.find((item) => item.id === id)?.label ?? id;

  return (
    <section className="detail-panel work-detail">
      <header className="detail-header work-detail-header">
        <div className="breadcrumb">
          <span>Portfolio</span>
          <ChevronRight size={13} />
          <span>{work.status}</span>
        </div>
        <div className="work-title-row">
          <div>
            <h2>{work.title}</h2>
            <p>{work.objective || "No objective has been recorded."}</p>
          </div>
          <span className={`work-status ${work.status}`}>{work.status}</span>
        </div>
        <div className="work-tabs">
          <button
            className={tab === "overview" ? "active" : ""}
            onClick={() => setTab("overview")}
          >
            <ListTree size={14} />
            Overview
          </button>
          <button
            className={tab === "graph" ? "active" : ""}
            onClick={() => setTab("graph")}
          >
            <Network size={14} />
            Relationship graph
          </button>
          <button
            className={tab === "manual" ? "active" : ""}
            onClick={() => setTab("manual")}
          >
            <RadioTower size={14} />
            Manual Bridge
          </button>
          {props.onCoordinate && (
            <button
              onClick={() =>
                props.onCoordinate?.(
                  work.id,
                  roles.find((role) => role.role_type === "work_coordinator")
                    ?.id,
                )
              }
            >
              <Sparkles size={14} />
              Ask coordinator
            </button>
          )}
          <button className="edit-work" onClick={() => setEditing(!editing)}>
            {editing ? "Cancel" : "Edit work"}
          </button>
        </div>
      </header>
      {tab === "graph" ? (
        <div className="graph-pane">
          <WorkGraph
            work={work}
            conversations={work.conversations}
            roles={roles}
            relationships={scopedRelationships}
          />
          <RelationshipList
            relationships={scopedRelationships}
            entityName={entityName}
            onDelete={props.onDeleteRelationship}
          />
        </div>
      ) : tab === "manual" ? (
        <ManualBridge workId={work.id} />
      ) : (
        <div className="detail-scroll work-overview">
          {editing && (
            <section className="work-section editor-card">
              <div className="section-title">
                <Save size={15} />
                <h3>Edit work item</h3>
              </div>
              <label className="field-label">
                Objective
                <textarea
                  value={objective}
                  onChange={(event) => setObjective(event.target.value)}
                  rows={3}
                />
              </label>
              <label className="field-label">
                Status
                <select
                  value={work.status}
                  onChange={(event) =>
                    props.onUpdate({
                      status: event.target.value as WorkItemInput["status"],
                    })
                  }
                >
                  <option value="planned">Planned</option>
                  <option value="active">Active</option>
                  <option value="blocked">Blocked</option>
                  <option value="completed">Completed</option>
                  <option value="archived">Archived</option>
                </select>
              </label>
              <button
                className="primary-button compact"
                disabled={props.saving}
                onClick={() => {
                  props.onUpdate({ objective: objective.trim() });
                  setEditing(false);
                }}
              >
                <Save size={13} />
                Save objective
              </button>
            </section>
          )}

          <section className="work-section">
            <div className="section-title">
              <MessageSquareText size={15} />
              <h3>Associated conversations</h3>
              <span>{work.conversations.length}</span>
            </div>
            <div className="attach-row">
              <select
                aria-label="Conversation to attach"
                value={attachId}
                onChange={(event) => setAttachId(event.target.value)}
              >
                <option value="">Choose a conversation…</option>
                {unattached.map((conversation) => (
                  <option key={conversation.id} value={conversation.id}>
                    {conversation.catalog_title ||
                      conversation.title ||
                      conversation.id}
                  </option>
                ))}
              </select>
              <button
                className="secondary-button compact"
                disabled={!attachId}
                onClick={() => {
                  props.onAttach(attachId);
                  setAttachId("");
                }}
              >
                <Plus size={13} />
                Attach
              </button>
            </div>
            <div className="associated-grid">
              {work.conversations.map((conversation) => (
                <article className="associated-card" key={conversation.id}>
                  <span className="provider-mark">
                    <Bot size={15} />
                  </span>
                  <button
                    className="associated-copy"
                    onClick={() => props.onOpenConversation(conversation.id)}
                  >
                    <strong>
                      {conversation.catalog_title ||
                        conversation.title ||
                        "Untitled conversation"}
                    </strong>
                    <small>
                      {conversation.provider} ·{" "}
                      {conversation.source_kind || "native"}
                    </small>
                  </button>
                  <button
                    className="icon-button danger-hover"
                    title="Remove from work item"
                    onClick={() => props.onDetach(conversation.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </article>
              ))}
            </div>
          </section>

          <section className="work-section">
            <div className="section-title">
              <GitBranch size={15} />
              <h3>Durable coordinator roles</h3>
              <span>{roles.length}</span>
            </div>
            <div className="role-tree">
              {roles.length ? (
                roles.map((role) => (
                  <RoleCard
                    key={role.id}
                    role={role}
                    onOpenConversation={props.onOpenConversation}
                    parent={roles.find(
                      (candidate) => candidate.id === role.parent_role_id,
                    )}
                  />
                ))
              ) : (
                <p className="muted">
                  No coordinator roles yet. Roles persist independently of the
                  conversations that inhabit them.
                </p>
              )}
            </div>
            <div className="compact-form three">
              <select
                aria-label="Role type"
                value={roleType}
                onChange={(event) =>
                  setRoleType(event.target.value as RoleInput["role_type"])
                }
              >
                <option value="work_coordinator">Work coordinator</option>
                <option value="worker">Worker</option>
                <option value="specialist">Specialist</option>
              </select>
              <select
                aria-label="Parent role"
                value={parentRoleId}
                onChange={(event) => setParentRoleId(event.target.value)}
              >
                <option value="">No reporting parent</option>
                {roles.map((role) => (
                  <option key={role.id} value={role.id}>
                    {role.charter}
                  </option>
                ))}
              </select>
              <input
                aria-label="Role charter"
                value={charter}
                onChange={(event) => setCharter(event.target.value)}
                placeholder="Coordinate implementation and validation"
              />
              <button
                className="secondary-button compact"
                disabled={!charter.trim()}
                onClick={submitRole}
              >
                <Plus size={13} />
                Add role
              </button>
            </div>
          </section>

          <section className="work-section">
            <div className="section-title">
              <Link2 size={15} />
              <h3>Relationships</h3>
              <span>{scopedRelationships.length}</span>
            </div>
            <RelationshipList
              relationships={scopedRelationships}
              entityName={entityName}
              onDelete={props.onDeleteRelationship}
            />
            <div className="compact-form relationship-form">
              <select
                aria-label="Relationship source"
                value={sourceId}
                onChange={(event) => setSourceId(event.target.value)}
              >
                <option value="">Source…</option>
                {entityOptions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
              <select
                aria-label="Relationship kind"
                value={relationshipKind}
                onChange={(event) => setRelationshipKind(event.target.value)}
              >
                <option value="coordinates">coordinates</option>
                <option value="reports_to">reports to</option>
                <option value="depends_on">depends on</option>
                <option value="audits">audits</option>
                <option value="collaborates_with">collaborates with</option>
                <option value="related_to">related to</option>
              </select>
              <select
                aria-label="Relationship target"
                value={targetId}
                onChange={(event) => setTargetId(event.target.value)}
              >
                <option value="">Target…</option>
                {entityOptions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
              <button
                className="secondary-button compact"
                disabled={!sourceId || !targetId || sourceId === targetId}
                onClick={submitRelationship}
              >
                <Plus size={13} />
                Link
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

function RoleCard({
  role,
  parent,
  onOpenConversation,
}: {
  role: CoordinatorRole;
  parent?: CoordinatorRole;
  onOpenConversation: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <article className={`role-card ${expanded ? "expanded" : ""}`}>
      <span className={`role-state ${role.status}`} />
      <div>
        <button
          className="role-card-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          <span>
            <strong>{role.charter}</strong>
            <small>
              {role.role_type.replaceAll("_", " ")} · {role.status} · checkpoint{" "}
              {role.checkpoint_version}
            </small>
          </span>
          {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
        {parent && <span>Reports to {parent.charter}</span>}
        {expanded && (
          <dl className="role-card-details">
            <div>
              <dt>Role ID</dt>
              <dd>{role.id}</dd>
            </div>
            <div>
              <dt>Scope</dt>
              <dd>{role.scope || role.work_item_id || "Not set"}</dd>
            </div>
            <div>
              <dt>Autonomy</dt>
              <dd>{role.autonomy_mode || "delegate"}</dd>
            </div>
            <div>
              <dt>Authority</dt>
              <dd>{role.authority_profile || "Not set"}</dd>
            </div>
            <div>
              <dt>Conversation</dt>
              <dd>
                {role.current_conversation_id ? (
                  <button
                    className="role-conversation-link"
                    onClick={() =>
                      onOpenConversation(role.current_conversation_id!)
                    }
                  >
                    <MessageSquareText size={12} />
                    {role.current_conversation_id}
                  </button>
                ) : (
                  "Not attached"
                )}
              </dd>
            </div>
            <div className="wide">
              <dt>Charter</dt>
              <dd>{role.charter}</dd>
            </div>
          </dl>
        )}
      </div>
      <em>{role.authority_profile || "authority unset"}</em>
    </article>
  );
}

function RelationshipList({
  relationships,
  entityName,
  onDelete,
}: {
  relationships: WorkRelationship[];
  entityName: (id: string) => string;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="relationship-list">
      {relationships.length ? (
        relationships.map((relationship) => (
          <div key={relationship.id}>
            <span title={entityName(relationship.source_id)}>
              {entityName(relationship.source_id)}
            </span>
            <em>
              {relationship.label || relationship.kind.replaceAll("_", " ")}
            </em>
            <span title={entityName(relationship.target_id)}>
              {entityName(relationship.target_id)}
            </span>
            <button
              className="icon-button danger-hover"
              title="Delete relationship"
              onClick={() => onDelete(relationship.id)}
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))
      ) : (
        <p className="muted">No explicit relationships yet.</p>
      )}
    </div>
  );
}
