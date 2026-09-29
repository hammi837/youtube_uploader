import { useEffect, useState } from 'react';
import {
  addPlanItem,
  approvePlan,
  checkPlanCompletion,
  createPlan,
  deletePlan,
  generatePlanScripts,
  getPlan,
  listPlanItems,
  listPlans,
  rejectPlan,
  removePlanItem,
} from '../services/api';
import type {
  ContentPlan,
  ContentPlanCreate,
  PlanApproveRequest,
  PlanItem,
  PlanItemCreate,
} from '../types/api';

const POLL_MS = 3000;

const PLAN_STATUS_LABEL: Record<string, string> = {
  draft: '📝 Draft',
  approved: '✅ Approved',
  completed: '🎉 Completed',
  rejected: '🚫 Rejected',
};

const ITEM_STATUS_LABEL: Record<string, string> = {
  planned: '📋 Planned',
  pending: '📄 Script Ready',
  researching: '🔍 Researching',
  generating: '⚙ Generating',
  completed: '✅ Completed',
  failed: '❌ Failed',
};

function fmt(dt: string | null): string {
  if (!dt) return '—';
  return new Date(dt).toLocaleString();
}

type View = 'list' | 'create' | 'detail';

export function PlannerPage() {
  const [view, setView] = useState<View>('list');
  const [plans, setPlans] = useState<ContentPlan[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<ContentPlan | null>(null);
  const [items, setItems] = useState<PlanItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Load plans on mount
  useEffect(() => {
    loadPlans();
  }, []);

  // Load plan detail when selected
  useEffect(() => {
    if (selectedPlanId) {
      loadPlanDetail(selectedPlanId);
    }
  }, [selectedPlanId]);

  // Poll for plan completion if approved
  useEffect(() => {
    if (!selectedPlan || selectedPlan.status !== 'approved') return;

    const interval = setInterval(async () => {
      try {
        const completion = await checkPlanCompletion(selectedPlan.id);
        if (completion.complete) {
          // Refresh plan status
          const updated = await getPlan(selectedPlan.id);
          setSelectedPlan(updated);
          setPlans(plans.map(p => p.id === updated.id ? updated : p));
          clearInterval(interval);
        }
      } catch (e) {
        console.error('Failed to check completion:', e);
      }
    }, POLL_MS);

    return () => clearInterval(interval);
  }, [selectedPlan, plans]);

  async function loadPlans() {
    try {
      setLoading(true);
      const data = await listPlans();
      setPlans(data);
      setError(null);
    } catch (e) {
      setError(`Failed to load plans: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadPlanDetail(planId: string) {
    try {
      setLoading(true);
      const [plan, itemsData] = await Promise.all([
        getPlan(planId),
        listPlanItems(planId),
      ]);
      setSelectedPlan(plan);
      setItems(itemsData);
      setError(null);
    } catch (e) {
      setError(`Failed to load plan: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  function handleCreatePlan() {
    setView('create');
  }

  function handleSelectPlan(planId: string) {
    setSelectedPlanId(planId);
    setView('detail');
  }

  function handleBack() {
    setView('list');
    setSelectedPlanId(null);
    setSelectedPlan(null);
    setItems([]);
  }

  async function handleDeletePlan(planId: string) {
    if (!confirm('Are you sure you want to delete this plan?')) return;

    try {
      setLoading(true);
      await deletePlan(planId);
      await loadPlans();
      handleBack();
    } catch (e) {
      setError(`Failed to delete plan: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="planner-page">
      <div className="content-subnav">
        <button
          className={`nav-tab ${view === 'list' ? 'nav-tab--active' : ''}`}
          onClick={() => setView('list')}
        >
          📅 Plans
        </button>
        {view === 'create' && (
          <button className="nav-tab nav-tab--active">➕ Create Plan</button>
        )}
        {view === 'detail' && selectedPlan && (
          <>
            <button className="nav-tab nav-tab--active">{selectedPlan.name}</button>
            <button className="nav-tab" onClick={handleBack}>← Back</button>
          </>
        )}
      </div>

      {error && (
        <div className="form-error" style={{ margin: '1rem 0', padding: '0.75rem', background: 'var(--color-error-bg)', color: 'var(--color-error)', borderRadius: '4px' }}>
          {error}
        </div>
      )}

      {loading && <div className="loading">Loading...</div>}

      {view === 'list' && (
        <PlanList
          plans={plans}
          onSelect={handleSelectPlan}
          onCreate={handleCreatePlan}
          onDelete={handleDeletePlan}
        />
      )}

      {view === 'create' && (
        <CreatePlanForm
          onCreated={(planId) => {
            handleSelectPlan(planId);
          }}
          onCancel={handleBack}
        />
      )}

      {view === 'detail' && selectedPlan && (
        <PlanDetail
          plan={selectedPlan}
          items={items}
          onRefresh={() => loadPlanDetail(selectedPlan.id)}
          onUpdate={(plan) => setSelectedPlan(plan)}
          onItemsChange={() => loadPlanDetail(selectedPlan.id)}
        />
      )}
    </div>
  );
}

// ── Plan List ────────────────────────────────────────────────────────────────

function PlanList({
  plans,
  onSelect,
  onCreate,
  onDelete,
}: {
  plans: ContentPlan[];
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="plan-list">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h2>Content Plans</h2>
        <button className="btn btn--primary" onClick={onCreate}>
          ➕ Create Plan
        </button>
      </div>

      {plans.length === 0 ? (
        <div className="empty-state">
          <p>No plans yet. Create your first plan to get started.</p>
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Items</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {plans.map((plan) => (
              <tr key={plan.id}>
                <td>
                  <button
                    className="link-button"
                    onClick={() => onSelect(plan.id)}
                    style={{ textAlign: 'left', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', textDecoration: 'underline' }}
                  >
                    {plan.name}
                  </button>
                </td>
                <td>{PLAN_STATUS_LABEL[plan.status] || plan.status}</td>
                <td>{plan.item_count}</td>
                <td>{fmt(plan.created_at)}</td>
                <td>
                  <button className="btn btn--small" onClick={() => onSelect(plan.id)}>
                    View
                  </button>
                  {plan.status === 'draft' && (
                    <button
                      className="btn btn--small btn--danger"
                      onClick={() => onDelete(plan.id)}
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Create Plan Form ───────────────────────────────────────────────────────────

function CreatePlanForm({
  onCreated,
  onCancel,
}: {
  onCreated: (planId: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [scheduleStart, setScheduleStart] = useState('');
  const [interval, setInterval] = useState(1440);
  const [timezone, setTimezone] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError('Plan name is required');
      return;
    }

    try {
      setLoading(true);
      const data: ContentPlanCreate = {
        name: name.trim(),
        description: description.trim() || undefined,
        schedule_start: scheduleStart || undefined,
        schedule_interval_minutes: interval,
        schedule_timezone: timezone || undefined,
      };
      const plan = await createPlan(data);
      onCreated(plan.id);
    } catch (e) {
      setError(`Failed to create plan: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="form-container">
      <h2>Create Content Plan</h2>

      {error && (
        <div className="form-error" style={{ margin: '1rem 0', padding: '0.75rem', background: 'var(--color-error-bg)', color: 'var(--color-error)', borderRadius: '4px' }}>
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label htmlFor="plan-name">Plan Name *</label>
          <input
            id="plan-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g., October 2026 Content Plan"
            required
          />
        </div>

        <div className="form-group">
          <label htmlFor="plan-description">Description</label>
          <textarea
            id="plan-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional description of this plan"
            rows={3}
          />
        </div>

        <div className="form-group">
          <label htmlFor="schedule-start">Schedule Start (optional)</label>
          <input
            id="schedule-start"
            type="datetime-local"
            value={scheduleStart}
            onChange={(e) => setScheduleStart(e.target.value)}
          />
          <small>Format: YYYY-MM-DDTHH:MM. Leave empty to set later.</small>
        </div>

        <div className="form-group">
          <label htmlFor="schedule-interval">Interval (minutes)</label>
          <input
            id="schedule-interval"
            type="number"
            value={interval}
            onChange={(e) => setInterval(parseInt(e.target.value) || 1440)}
            min={5}
            max={10080}
          />
          <small>Default: 1440 minutes (1 day)</small>
        </div>

        <div className="form-group">
          <label htmlFor="schedule-timezone">Timezone (optional)</label>
          <input
            id="schedule-timezone"
            type="text"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            placeholder="e.g., Asia/Karachi, America/New_York"
          />
          <small>IANA timezone name. Leave empty for UTC.</small>
        </div>

        <div className="form-actions">
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn--primary" disabled={loading}>
            {loading ? 'Creating...' : 'Create Plan'}
          </button>
        </div>
      </form>
    </div>
  );
}

// ── Plan Detail ───────────────────────────────────────────────────────────────

type DetailView = 'overview' | 'items' | 'scripts';

function PlanDetail({
  plan,
  items,
  onRefresh,
  onUpdate,
  onItemsChange,
}: {
  plan: ContentPlan;
  items: PlanItem[];
  onRefresh: () => void;
  onUpdate: (plan: ContentPlan) => void;
  onItemsChange: () => void;
}) {
  const [detailView, setDetailView] = useState<DetailView>('overview');
  const [error, setError] = useState<string | null>(null);

  async function handleGenerateScripts() {
    if (!confirm('Generate scripts for all planned items? This may take several minutes.')) return;

    try {
      setError(null);
      const result = await generatePlanScripts(plan.id);
      if (result.failed > 0) {
        setError(`${result.failed} items failed to generate scripts. See item details for errors.`);
      }
      onItemsChange();
    } catch (e) {
      setError(`Failed to generate scripts: ${(e as Error).message}`);
    }
  }

  async function handleApprove() {
    if (!confirm(`Approve plan and queue ${items.length} items? This will create queue jobs that will execute sequentially.`)) return;

    const request: PlanApproveRequest = {
      schedule_start: plan.schedule_start || undefined,
      schedule_interval_minutes: plan.schedule_interval_minutes || undefined,
      schedule_timezone: plan.schedule_timezone || undefined,
    };

    try {
      setError(null);
      const result = await approvePlan(plan.id, request);
      alert(`Approved! ${result.queued_count} items queued, ${result.skipped_count} skipped.\n\nSchedule:\n${result.schedule_summary.join('\n')}`);
      onUpdate(await getPlan(plan.id));
      onItemsChange();
    } catch (e) {
      setError(`Failed to approve plan: ${(e as Error).message}`);
    }
  }

  async function handleReject() {
    if (!confirm('Reject this plan? This will prevent queueing any items.')) return;

    try {
      setError(null);
      await rejectPlan(plan.id);
      onUpdate(await getPlan(plan.id));
    } catch (e) {
      setError(`Failed to reject plan: ${(e as Error).message}`);
    }
  }

  const scriptReadyCount = items.filter(i => i.has_script).length;

  return (
    <div className="plan-detail">
      <div className="content-subnav">
        <button
          className={`nav-tab ${detailView === 'overview' ? 'nav-tab--active' : ''}`}
          onClick={() => setDetailView('overview')}
        >
          📋 Overview
        </button>
        <button
          className={`nav-tab ${detailView === 'items' ? 'nav-tab--active' : ''}`}
          onClick={() => setDetailView('items')}
        >
          📦 Items ({items.length})
        </button>
        <button
          className={`nav-tab ${detailView === 'scripts' ? 'nav-tab--active' : ''}`}
          onClick={() => setDetailView('scripts')}
        >
          📄 Scripts ({scriptReadyCount})
        </button>
        <span style={{ marginLeft: 'auto', fontSize: '0.9em', color: 'var(--color-muted)' }}>
          {getPlannedCount(items)} need scripts
        </span>
      </div>

      {error && (
        <div className="form-error" style={{ margin: '1rem 0', padding: '0.75rem', background: 'var(--color-error-bg)', color: 'var(--color-error)', borderRadius: '4px' }}>
          {error}
        </div>
      )}

      {detailView === 'overview' && (
        <PlanOverview
          plan={plan}
          items={items}
          plannedCount={items.filter(i => i.status === 'planned').length}
          onGenerateScripts={handleGenerateScripts}
          onApprove={handleApprove}
          onReject={handleReject}
          onRefresh={onRefresh}
        />
      )}

      {detailView === 'items' && (
        <PlanItems plan={plan} items={items} onChange={onItemsChange} />
      )}

      {detailView === 'scripts' && (
        <PlanScripts items={items} />
      )}
    </div>
  );
}

// Calculate planned count locally where needed
function getPlannedCount(items: PlanItem[]): number {
  return items.filter(i => i.status === 'planned').length;
}

// ── Plan Overview ─────────────────────────────────────────────────────────────

function PlanOverview({
  plan,
  items,
  plannedCount,
  onGenerateScripts,
  onApprove,
  onReject,
  onRefresh,
}: {
  plan: ContentPlan;
  items: PlanItem[];
  plannedCount: number;
  onGenerateScripts: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRefresh: () => void;
}) {
  const scriptReadyCount = items.filter(i => i.has_script).length;

  return (
    <div className="plan-overview">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h2>{plan.name}</h2>
        <button className="btn btn--small" onClick={onRefresh}>
          🔄 Refresh
        </button>
      </div>

      <div className="plan-meta">
        <p><strong>Status:</strong> {PLAN_STATUS_LABEL[plan.status] || plan.status}</p>
        <p><strong>Items:</strong> {items.length}</p>
        <p><strong>Created:</strong> {fmt(plan.created_at)}</p>
        {plan.description && <p><strong>Description:</strong> {plan.description}</p>}
        {plan.schedule_start && (
          <p><strong>Schedule Start:</strong> {fmt(plan.schedule_start)}</p>
        )}
        {plan.schedule_interval_minutes && (
          <p><strong>Interval:</strong> {plan.schedule_interval_minutes} minutes</p>
        )}
        {plan.schedule_timezone && (
          <p><strong>Timezone:</strong> {plan.schedule_timezone}</p>
        )}
      </div>

      <div className="plan-stats">
        <div className="stat-card">
          <div className="stat-value">{items.length}</div>
          <div className="stat-label">Total Items</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{scriptReadyCount}</div>
          <div className="stat-label">Scripts Ready</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{plannedCount}</div>
          <div className="stat-label">Need Scripts</div>
        </div>
      </div>

      <div className="plan-actions">
        {plan.status === 'draft' && (
          <>
            <button
              className="btn btn--primary"
              onClick={onGenerateScripts}
              disabled={plannedCount === 0}
            >
              📄 Generate Scripts
            </button>
            <button
              className="btn btn--success"
              onClick={onApprove}
              disabled={scriptReadyCount === 0}
            >
              ✅ Approve & Queue
            </button>
            <button className="btn btn--danger" onClick={onReject}>
              🚫 Reject
            </button>
          </>
        )}
        {plan.status === 'approved' && (
          <p className="status-message">Plan approved. Items are being processed in the queue.</p>
        )}
        {plan.status === 'completed' && (
          <p className="status-message">Plan completed. All items have finished processing.</p>
        )}
        {plan.status === 'rejected' && (
          <p className="status-message">Plan rejected. Items will not be queued.</p>
        )}
      </div>
    </div>
  );
}

// ── Plan Items ────────────────────────────────────────────────────────────────

function PlanItems({
  plan,
  items,
  onChange,
}: {
  plan: ContentPlan;
  items: PlanItem[];
  onChange: () => void;
}) {
  const [showAddForm, setShowAddForm] = useState(false);
  const [topic, setTopic] = useState('');
  const [language, setLanguage] = useState('en');
  const [tone, setTone] = useState('engaging');
  const [duration, setDuration] = useState(180);
  const [scenes, setScenes] = useState(12);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleAddItem(e: React.FormEvent) {
    e.preventDefault();
    if (!topic.trim()) {
      setError('Topic is required');
      return;
    }

    try {
      setLoading(true);
      const data: PlanItemCreate = {
        topic: topic.trim(),
        language,
        tone,
        target_duration_seconds: duration,
        scene_count: scenes,
      };
      await addPlanItem(plan.id, data);
      setTopic('');
      setShowAddForm(false);
      setError(null);
      onChange();
    } catch (e) {
      setError(`Failed to add item: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleRemoveItem(itemId: string) {
    if (!confirm('Remove this item from the plan?')) return;

    try {
      await removePlanItem(plan.id, itemId);
      onChange();
    } catch (e) {
      setError(`Failed to remove item: ${(e as Error).message}`);
    }
  }

  return (
    <div className="plan-items">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h3>Plan Items</h3>
        {!showAddForm && plan.status === 'draft' && (
          <button className="btn btn--primary" onClick={() => setShowAddForm(true)}>
            ➕ Add Item
          </button>
        )}
      </div>

      {error && (
        <div className="form-error" style={{ margin: '1rem 0', padding: '0.75rem', background: 'var(--color-error-bg)', color: 'var(--color-error)', borderRadius: '4px' }}>
          {error}
        </div>
      )}

      {showAddForm && (
        <form onSubmit={handleAddItem} style={{ marginBottom: '1rem', padding: '1rem', background: 'var(--color-muted-bg)', borderRadius: '4px' }}>
          <div className="form-group">
            <label>Topic *</label>
            <input
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="Enter topic"
              required
            />
          </div>
          <div className="form-group">
            <label>Language</label>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="en">English</option>
              <option value="es">Spanish</option>
              <option value="fr">French</option>
              <option value="de">German</option>
            </select>
          </div>
          <div className="form-group">
            <label>Tone</label>
            <select value={tone} onChange={(e) => setTone(e.target.value)}>
              <option value="engaging">Engaging</option>
              <option value="informative">Informative</option>
              <option value="educational">Educational</option>
              <option value="entertaining">Entertaining</option>
            </select>
          </div>
          <div className="form-group">
            <label>Duration (seconds)</label>
            <input
              type="number"
              value={duration}
              onChange={(e) => setDuration(parseInt(e.target.value) || 180)}
              min={30}
              max={3600}
            />
          </div>
          <div className="form-group">
            <label>Scene Count</label>
            <input
              type="number"
              value={scenes}
              onChange={(e) => setScenes(parseInt(e.target.value) || 12)}
              min={3}
              max={30}
            />
          </div>
          <div className="form-actions">
            <button type="button" className="btn" onClick={() => setShowAddForm(false)}>
              Cancel
            </button>
            <button type="submit" className="btn btn--primary" disabled={loading}>
              {loading ? 'Adding...' : 'Add Item'}
            </button>
          </div>
        </form>
      )}

      {items.length === 0 ? (
        <div className="empty-state">
          <p>No items in this plan yet. Add items to get started.</p>
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Topic</th>
              <th>Status</th>
              <th>Language</th>
              <th>Duration</th>
              <th>Scenes</th>
              <th>Script</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{item.topic}</td>
                <td>{ITEM_STATUS_LABEL[item.status] || item.status}</td>
                <td>{item.language}</td>
                <td>{item.target_duration_seconds}s</td>
                <td>{item.scene_count}</td>
                <td>{item.has_script ? '✅' : '—'}</td>
                <td>
                  {plan.status === 'draft' && (
                    <button
                      className="btn btn--small btn--danger"
                      onClick={() => handleRemoveItem(item.id)}
                    >
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Plan Scripts ──────────────────────────────────────────────────────────────

function PlanScripts({
  items,
}: {
  items: PlanItem[];
}) {
  const scriptReadyItems = items.filter(i => i.has_script);

  return (
    <div className="plan-scripts">
      <h3>Generated Scripts</h3>

      {scriptReadyItems.length === 0 ? (
        <div className="empty-state">
          <p>No scripts generated yet. Generate scripts from the Overview tab.</p>
        </div>
      ) : (
        <div className="script-list">
          {scriptReadyItems.map((item) => (
            <div key={item.id} className="script-item" style={{ padding: '1rem', marginBottom: '0.5rem', background: 'var(--color-muted-bg)', borderRadius: '4px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <strong>{item.topic}</strong>
                  {item.script_title && <div style={{ fontSize: '0.9em', color: 'var(--color-muted)' }}>{item.script_title}</div>}
                </div>
                <div style={{ fontSize: '0.9em' }}>
                  {ITEM_STATUS_LABEL[item.status] || item.status}
                </div>
              </div>
              {item.error_message && (
                <div style={{ marginTop: '0.5rem', color: 'var(--color-error)', fontSize: '0.9em' }}>
                  Error: {item.error_message}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
