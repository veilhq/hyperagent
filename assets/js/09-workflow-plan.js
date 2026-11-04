// Workflow launch approval: the agent wants to start a multi-agent workflow. Show its script, the
// limits it would run under, and hold the launch until the Operator allows or rejects it.
var workflowPlanEls = {};

function workflowPlanSpan(className, text) {
  var el = document.createElement('span');
  el.className = className;
  el.textContent = text;
  return el;
}

function workflowPlanFact(label, value, over) {
  var fact = document.createElement('div');
  fact.className = 'workflow-plan-fact';
  if (over) fact.setAttribute('data-over', 'true');
  fact.appendChild(workflowPlanSpan('workflow-plan-fact-label', label));
  fact.appendChild(workflowPlanSpan('workflow-plan-fact-value', value));
  return fact;
}

function workflowPlanDiv(className, text) {
  var el = document.createElement('div');
  el.className = className;
  if (text) el.textContent = text;
  return el;
}

function answerWorkflowPlan(data, allow) {
  var a = window.pywebview && pywebview.api;
  if (!a || !a.answer_workflow_plan) return;
  a.answer_workflow_plan(data.toolCallId, allow, data._tabId || null);
}

function closeWorkflowPlan(callId) {
  var el = workflowPlanEls[callId];
  if (!el) return;
  delete workflowPlanEls[callId];
  el.remove();
}

window.__acpWorkflowPlan = function(data) {
  if (!data || !data.toolCallId || workflowPlanEls[data.toolCallId]) return;
  var limits = data.limits || {};
  var overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.setAttribute('data-testid', 'workflow-plan');
  var panel = document.createElement('div');
  panel.className = 'workflow-plan-panel modal-panel';
  panel.setAttribute('role', 'alertdialog');

  var tab = data._tabId && typeof tabs !== 'undefined' && tabs[data._tabId] ? tabs[data._tabId].title : '';
  var header = workflowPlanDiv('workflow-plan-header');
  var kicker = workflowPlanDiv('workflow-plan-kicker', 'Workflow launch');
  if (tab) kicker.appendChild(workflowPlanSpan('workflow-plan-kicker-tab', tab));
  header.appendChild(kicker);
  header.appendChild(workflowPlanDiv('workflow-plan-title', data.name || 'Untitled workflow'));
  if (data.description) header.appendChild(workflowPlanDiv('workflow-plan-desc', data.description));
  panel.appendChild(header);

  var body = workflowPlanDiv('workflow-plan-body');
  var overCap = data.agentCalls > limits.agentCap;
  var facts = workflowPlanDiv('workflow-plan-facts');
  facts.appendChild(workflowPlanFact('Agent calls', String(data.agentCalls), overCap));
  if (data.phases && data.phases.length) facts.appendChild(workflowPlanFact('Phases', String(data.phases.length)));
  facts.appendChild(workflowPlanFact('Agent cap', String(limits.agentCap)));
  facts.appendChild(workflowPlanFact('Token budget', '+' + Math.round((limits.budget || 0) / 1000) + 'k'));
  body.appendChild(facts);

  var warns = workflowPlanDiv('workflow-plan-warns');
  if (data.fansOut) {
    warns.appendChild(workflowPlanDiv('workflow-plan-warn',
      'Loops or fan-out: the real agent count depends on the data, not the call sites. Spend is stopped at the budget.'));
  }
  if (overCap) {
    warns.appendChild(workflowPlanDiv('workflow-plan-warn', 'The script has more agent calls than your cap.'));
  }
  if (warns.children.length) body.appendChild(warns);

  var section = workflowPlanDiv('workflow-plan-section');
  section.appendChild(workflowPlanDiv('workflow-plan-section-label', 'Script'));
  var script = document.createElement('pre');
  script.className = 'workflow-plan-script';
  script.textContent = data.script;
  section.appendChild(script);
  body.appendChild(section);
  panel.appendChild(body);

  var actions = workflowPlanDiv('workflow-plan-actions');
  [['Reject', 'action-button action-button-ghost', false], ['Allow', 'action-button action-button-primary', true]]
    .forEach(function(spec) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = spec[1];
      btn.textContent = spec[0];
      btn.setAttribute('data-testid', 'workflow-plan-' + spec[0].toLowerCase());
      btn.addEventListener('click', function() { answerWorkflowPlan(data, spec[2]); });
      actions.appendChild(btn);
    });
  panel.appendChild(actions);

  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  workflowPlanEls[data.toolCallId] = overlay;
  requestAnimationFrame(function() { overlay.classList.add('visible'); });
  if (data._tabId && data._tabId !== activeTabId && window.HvToast) {
    window.HvToast.show({ variant: 'warn', title: (tab || 'A tab') + ' wants to launch a workflow', message: 'Its launch is waiting for your approval.' });
  }
};

window.__acpWorkflowPlanClosed = function(data) {
  if (data && data.toolCallId) closeWorkflowPlan(data.toolCallId);
};
