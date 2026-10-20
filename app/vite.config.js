const logApiTarget = process.env.LOG_API_PROXY_TARGET || 'http://127.0.0.1:8181';
const snapshotApiTarget = process.env.SNAPSHOT_API_PROXY_TARGET || 'http://127.0.0.1:8080';

export default {
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api/v1/logs': {
        target: logApiTarget,
        changeOrigin: false,
      },
      '/api/': {
        target: snapshotApiTarget,
        changeOrigin: false,
      },
    },
  },
};
