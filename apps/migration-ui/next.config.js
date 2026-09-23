/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  async redirects() {
    return [
      {
        source: '/discovery',
        destination: '/settings/discovery',
        permanent: false,
      },
      {
        source: '/readiness',
        destination: '/settings',
        permanent: false,
      },
      {
        source: '/workflows',
        destination: '/settings/migrate',
        permanent: false,
      },
      {
        source: '/validation',
        destination: '/settings/migrate',
        permanent: false,
      },
    ];
  },
};

module.exports = nextConfig;
