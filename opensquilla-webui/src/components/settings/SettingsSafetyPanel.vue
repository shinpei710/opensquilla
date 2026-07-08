<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useToasts } from '@/composables/useToasts'

const { t } = useI18n()
const { pushToast } = useToasts()

interface ModeOption {
  value: string
  label: string
  desc: string
}

const modeOptions = computed<ModeOption[]>(() => [
  { value: 'prompt', label: t('console.approvals.modePromptLabel'), desc: t('console.approvals.modePromptDesc') },
  { value: 'auto-approve', label: t('console.approvals.modeAutoApproveLabel'), desc: t('console.approvals.modeAutoApproveDesc') },
  { value: 'auto-deny', label: t('console.approvals.modeAutoDenyLabel'), desc: t('console.approvals.modeAutoDenyDesc') },
])

const mode = ref('prompt')
const loaded = ref(false)

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  let token = ''
  try { token = sessionStorage.getItem('opensquilla.wsToken') || '' } catch { /* no token */ }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

async function loadMode() {
  try {
    const res = await fetch('/api/approvals', { headers: authHeaders() })
    if (!res.ok) throw new Error('HTTP ' + res.status)
    const data = await res.json()
    if (typeof data?.mode === 'string') mode.value = data.mode
    loaded.value = true
  } catch {
    // Leave the default 'prompt' selected; the POST below is the source of truth.
    loaded.value = true
  }
}

async function onModeChange(next: string) {
  const previous = mode.value
  mode.value = next
  try {
    const res = await fetch('/api/approvals/settings', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ mode: next }),
    })
    if (!res.ok) throw new Error('HTTP ' + res.status)
    pushToast(t('console.approvals.toastStrategy', { mode: next }), { tone: 'ok' })
  } catch (err) {
    mode.value = previous
    pushToast(
      t('console.approvals.toastStrategyFailed', { msg: err instanceof Error ? err.message : String(err) }),
      { tone: 'danger' },
    )
  }
}

onMounted(loadMode)
</script>

<template>
  <section class="control-section">
    <div class="control-section__head">
      <h3 class="control-section__title">{{ t('settings.safety.title') }}</h3>
      <p class="control-section__desc">{{ t('settings.safety.desc') }}</p>
    </div>

    <div class="safety-options" role="radiogroup" :aria-label="t('console.approvals.strategyAriaLabel')">
      <label
        v-for="opt in modeOptions"
        :key="opt.value"
        class="safety-option"
        :class="{ 'is-active': mode === opt.value, 'is-risky': opt.value === 'auto-approve' }"
      >
        <input
          type="radio"
          name="approval-mode"
          class="safety-option__radio"
          :value="opt.value"
          :checked="mode === opt.value"
          @change="onModeChange(opt.value)"
        />
        <span class="safety-option__body">
          <span class="safety-option__label">{{ opt.label }}</span>
          <span class="safety-option__desc">{{ opt.desc }}</span>
        </span>
      </label>
    </div>
  </section>
</template>

<style scoped>
.safety-options {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.safety-option {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--bg-surface);
  cursor: pointer;
}

.safety-option:hover {
  border-color: var(--border-strong);
}

.safety-option.is-active {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 6%, var(--bg-surface));
}

.safety-option__radio {
  margin-top: 2px;
  accent-color: var(--accent);
}

.safety-option__body {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.safety-option__label {
  font-weight: 600;
  font-size: var(--fs-sm);
  color: var(--text);
}

.safety-option.is-risky.is-active .safety-option__label {
  color: var(--danger);
}

.safety-option__desc {
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
</style>
