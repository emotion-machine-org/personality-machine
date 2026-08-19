import posthog from 'posthog-js'

// Only initialize PostHog in production, and only when a key is configured (opt-in)
if (process.env.NODE_ENV === 'production' && process.env.NEXT_PUBLIC_POSTHOG_KEY) {
  posthog.init(process.env.NEXT_PUBLIC_POSTHOG_KEY, {
      api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST,
      defaults: '2025-05-24',
      // Disable console messages
      debug: false,
      verbose: false,
  });
} else {
  // In development, disable PostHog entirely
  console.log('[PostHog] Disabled in development mode');
}
