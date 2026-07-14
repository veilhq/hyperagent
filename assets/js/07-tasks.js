/* ===== Hyperagent: Task Sidebar ===== */
// Intercepts todo_list tool calls and displays a running task list in a side panel.

(function() {
  var taskPanel = null;
  var taskList = [];
  var taskDescription = '';

  function createTaskPanel() {
    if (taskPanel) return;
    taskPanel = document.createElement('div');
    taskPanel.id = 'task-panel';
    taskPanel.className = 'task-panel';
    taskPanel.innerHTML = '<div class="task-panel-header">Tasks</div><div class="task-panel-body" id="task-panel-body"></div>';
    var main = document.querySelector('.main-layout');
    if (main) main.appendChild(taskPanel);
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
      taskList = (data.tasks || []).map(function(t) { return { task_description: t.task_description, completed: false, id: t.id }; });
      renderTasks();
    } else if (data.command === 'complete') {
      var ids = data.completed_task_ids || [];
      for (var i = 0; i < taskList.length; i++) {
        if (ids.indexOf(taskList[i].id) > -1) taskList[i].completed = true;
      }
      renderTasks();
    } else if (data.command === 'add') {
      var newTasks = data.new_tasks || [];
      for (var i = 0; i < newTasks.length; i++) {
        taskList.push({ task_description: newTasks[i].task_description, completed: false, id: newTasks[i].id || String(taskList.length + 1) });
      }
      if (data.new_description) taskDescription = data.new_description;
      renderTasks();
    } else if (data.command === 'remove') {
      var removeIds = data.remove_task_ids || [];
      taskList = taskList.filter(function(t) { return removeIds.indexOf(t.id) < 0; });
      renderTasks();
    }
  };

  window.__acpTaskReset = function() {
    taskList = [];
    taskDescription = '';
    hideTaskPanel();
  };
})();


