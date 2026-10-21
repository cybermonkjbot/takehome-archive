import { readFileSync } from 'node:fs';

const logApiTarget = process.env.LOG_API_PROXY_TARGET || 'https://localhost:8444';
const snapshotApiTarget = process.env.SNAPSHOT_API_PROXY_TARGET || 'https://localhost:8444';

export default ({ command }) => {
  let webTls;
  if (command === 'serve') {
    const webCert = process.env.WEB_TLS_CERT_FILE;
    const webKey = process.env.WEB_TLS_KEY_FILE;
    if (!webCert || !webKey) {
      throw new Error('WEB_TLS_CERT_FILE and WEB_TLS_KEY_FILE are required; the dashboard must use HTTPS.');
    }
    webTls = { cert: readFileSync(webCert), key: readFileSync(webKey) };
  }

  return {
    server: {
      host: '127.0.0.1',
      allowedHosts: ['takehome.localhost'],
      https: webTls,
      proxy: {
        '/api/v1/logs': {
          target: logApiTarget,
          changeOrigin: false,
          secure: true,
        },
        '/api/': {
          target: snapshotApiTarget,
          changeOrigin: false,
          secure: true,
        },
      },
    },
  };
};
