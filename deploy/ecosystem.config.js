module.exports = {
  apps: [
    {
      name: 'isee-backend',
      cwd: './backend',
      script: '.venv/bin/uvicorn',
      args: 'app.main:app --host 0.0.0.0 --port 8000',
      env: {
        SCHEDULER_DISABLED: 'true',
      },
    },
    {
      name: 'isee-scheduler',
      cwd: './backend',
      script: '.venv/bin/python',
      args: '-m app.scheduler_runner',
      // Only one instance — the sidecar is a singleton.
      instances: 1,
    },
  ],
};
