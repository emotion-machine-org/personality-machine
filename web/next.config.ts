import type {NextConfig} from "next";

const previewPrId = process.env.VERCEL_ENV === "preview"
    ? process.env.VERCEL_GIT_PULL_REQUEST_ID
    : undefined;

const nextConfig: NextConfig = {
    // Expose PR id to the client only on preview builds.
    env: {
        ...(previewPrId ? {NEXT_PUBLIC_VERCEL_GIT_PULL_REQUEST_ID: previewPrId} : {}),
    },
    webpack: (config, {dev, isServer}) => {
        if (dev && !isServer) {
            // Optimize webpack for better HMR stability
            config.watchOptions = {
                poll: 1000,
                aggregateTimeout: 300,
            };

            // Improve module resolution
            config.resolve.fallback = {
                ...config.resolve.fallback,
            };
        }

        return config;
    },
    async redirects() {
        return [
            {
                source: '/analytics',
                destination: '/conversations',
                permanent: true,
            },
        ];
    },
    experimental: {
        // Disable webpack cache in development for better stability
        webpackBuildWorker: true,
    },
};

export default nextConfig;
