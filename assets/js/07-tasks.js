/* ===== Hyperagent: Task Sidebar ===== */
// Displays a running task list in a side panel, fed by kiro todo_list tool
// calls (__acpTaskUpdate) or ACP plan updates (__acpPlanUpdate).

var taskPanel = null;
var taskList = [];
var taskDescription = '';

function createTaskPanel() {
  if (taskPanel) return;
  taskPanel = document.createElement('div');
  taskPanel.id = 'task-panel';
  taskPanel.className = 'task-panel';
  taskPanel.innerHTML = '<div class="panel-header task-panel-header">Tasks</div><div class="task-panel-body" id="task-panel-body"></div>';
  // Toggle collapse on header click — shared HvCollapsible primitive; the panel
  // markup is owned here, the primitive attaches only the toggle behavior.
  var taskHeader = taskPanel.querySelector('.task-panel-header');
  if (window.HvCollapsible) {
    window.HvCollapsible.register({ header: taskHeader, panel: taskPanel });
  } else {
    taskHeader.addEventListener('click', function() {
      taskPanel.classList.toggle('collapsed');
    });
  }
  document.body.appendChild(taskPanel);
  // Trigger reflow before adding visible class (enables entry animation)
  void taskPanel.offsetWidth;
}

function renderTasks() {
  if (!taskPanel) createTaskPanel();
  taskPanel.classList.add('visible');
  var body = document.getElementById('task-panel-body');
  if (!body) return;
  var html = '';
  if (taskDescription) html += '<div class="task-panel-desc">' + taskDescription + '</div>';
  if (taskList.length) {
    html += '<ul class="task-panel-list">';
    for (var i = 0; i < taskList.length; i++) {
      var t = taskList[i];
      var cls = t.completed ? 'task-item done' : 'task-item';
      var check = t.completed ? '■' : '□';
      html += '<li class="' + cls + '"><span class="task-check">' + check + '</span>' + t.task_description + '</li>';
    }
    html += '</ul>';
  }
  body.innerHTML = html;
}

function hideTaskPanel() {
  if (taskPanel) taskPanel.classList.remove('visible');
}

// Hook into tool_call_update to intercept todo_list results
window.__acpTaskUpdate = function(data) {
  if (!data) return;
  if (data.command === 'create') {
    taskDescription = data.task_list_description || '';
    taskList = (data.tasks || []).map(function(t, i) { return { task_description: t.task_description, completed: false, id: t.id || String(i + 1) }; });
    renderTasks();
  } else if (data.command === 'complete') {
    var ids = data.completed_task_ids || [];
    for (var i = 0; i < taskList.length; i++) {
      if (ids.indexOf(taskList[i].id) > -1) taskList[i].completed = true;
    }
    renderTasks();
    // Auto-open if collapsed
    if (taskPanel && taskPanel.classList.contains('collapsed')) {
      taskPanel.classList.remove('collapsed');
    }
    // Highlight just-completed items
    highlightItems(ids);
    // Auto-dismiss if all tasks done
    checkAllDone();
  } else if (data.command === 'add') {
    var newTasks = data.new_tasks || [];
    for (var i = 0; i < newTasks.length; i++) {
      taskList.push({ task_description: newTasks[i].task_description, completed: false, id: newTasks[i].id || String(taskList.length + 1) });
    }
    if (data.new_description) taskDescription = data.new_description;
    renderTasks();
    // Auto-open if collapsed
    if (taskPanel && taskPanel.classList.contains('collapsed')) {
      taskPanel.classList.remove('collapsed');
    }
  } else if (data.command === 'remove') {
    var removeIds = data.remove_task_ids || [];
    taskList = taskList.filter(function(t) { return removeIds.indexOf(t.id) < 0; });
    renderTasks();
  }
};

// ACP agent plans (session/update "plan"). Every update carries the complete
// entry list and the client must replace its plan with it, so the panel is
// rebuilt from the entries rather than patched. Entries have no ids; their
// text is used to spot which ones just completed so the highlight and the
// all-done dismissal behave like the todo_list path. in_progress renders as
// not done, matching the panel's two states.
window.__acpPlanUpdate = function(entries) {
  if (!Array.isArray(entries)) return;
  var wasDone = {};
  for (var i = 0; i < taskList.length; i++) {
    if (taskList[i].completed) wasDone[taskList[i].task_description] = true;
  }
  taskDescription = '';
  taskList = entries.map(function(entry, idx) {
    return {
      task_description: (entry && entry.content) || '',
      completed: !!entry && entry.status === 'completed',
      id: String(idx + 1)
    };
  });
  if (!taskList.length) {
    hideTaskPanel();
    return;
  }
  renderTasks();
  var justDone = taskList
    .filter(function(t) { return t.completed && !wasDone[t.task_description]; })
    .map(function(t) { return t.id; });
  if (justDone.length) {
    if (taskPanel && taskPanel.classList.contains('collapsed')) {
      taskPanel.classList.remove('collapsed');
    }
    highlightItems(justDone);
    checkAllDone();
  }
};

function highlightItems(ids) {
  if (!taskPanel) return;
  var items = taskPanel.querySelectorAll('.task-item');
  items.forEach(function(el, idx) {
    if (idx < taskList.length && ids.indexOf(taskList[idx].id) > -1) {
      el.classList.add('just-done');
      setTimeout(function() { el.classList.remove('just-done'); }, 1500);
    }
  });
}

function checkAllDone() {
  if (!taskList.length) return;
  var allDone = taskList.every(function(t) { return t.completed; });
  if (allDone) {
    setTimeout(function() {
      if (taskPanel) {
        // Let it sit for a moment showing all done, then slide out
        taskPanel.classList.remove('visible');
      }
    }, 2500);
  }
}

window.__acpTaskReset = function() {
  taskList = [];
  taskDescription = '';
  hideTaskPanel();
};
