'use client'

import { useState } from 'react'
import { useSignIn } from '@clerk/nextjs'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'

export default function CustomSignIn() {
  const { isLoaded, signIn, setActive } = useSignIn()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')
  const router = useRouter()
  const searchParams = useSearchParams()
  const redirectParam = searchParams.get('redirect_url')
  const redirectTarget = redirectParam && redirectParam.startsWith('/') ? redirectParam : '/'
  const signUpHref = redirectParam
    ? `/sign-up?redirect_url=${encodeURIComponent(redirectParam)}`
    : '/sign-up'

  const handleGoogleSignIn = async () => {
    if (!isLoaded) return

    setError('')
    setIsLoading(true)
    try {
      await signIn.authenticateWithRedirect({
        strategy: 'oauth_google',
        redirectUrl: '/sso-callback',
        redirectUrlComplete: redirectTarget
      })
    } catch (err: unknown) {
      const error = err as { errors?: Array<{ message: string }> }
      setError(error.errors?.[0]?.message || 'An error occurred')
      setIsLoading(false)
    }
  }

  const handleEmailSignIn = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!isLoaded || !email || !password) return

    setIsLoading(true)
    setError('')

    try {
      const result = await signIn.create({
        identifier: email,
        password
      })

      if (result.status === 'complete') {
        await setActive({ session: result.createdSessionId })
        router.push(redirectTarget)
      }
    } catch (err: unknown) {
      const error = err as { errors?: Array<{ message: string }> }
      setError(error.errors?.[0]?.message || 'Invalid email or password')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-black flex items-center justify-center">
      <div className="flex flex-col items-center text-center px-6 w-full max-w-sm">
        <div className="mb-16">
          <h1 className="text-white text-4xl mb-4 font-normal">
            Personality<br />Machine
          </h1>
          <h2 className="text-white text-2xl mb-2 font-normal">
            Sign In
          </h2>
          <p className="text-white/60 text-sm font-book">
            Welcome back please sign in to continue.
          </p>
        </div>

        <div className="w-full space-y-6">
          <button
            onClick={handleGoogleSignIn}
            disabled={isLoading || !isLoaded}
            className="w-full bg-white text-black py-4 rounded-full font-medium hover:bg-white/80 transition-colors flex items-center justify-center space-x-2 disabled:opacity-50"
          >
            <svg className="w-5 h-5 " viewBox="0 0 24 24">
              <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
              <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
            <span className="translate-y-0.5">Continue with Google</span>
          </button>

          <div className="flex items-center space-x-4">
            <div className="flex-1 h-px bg-white/20"></div>
            <span className="text-white/60 text-sm font-book">or</span>
            <div className="flex-1 h-px bg-white/20"></div>
          </div>

          <form onSubmit={handleEmailSignIn} className="w-full space-y-4">
            <input
              type="email"
              placeholder="Enter email address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-[#3C3C3C] text-white px-5 py-4 rounded-full placeholder-white border-0 focus:ring-none focus:outline-none"
              disabled={isLoading}
            />

            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-[#3C3C3C] text-white px-5 py-4 rounded-full placeholder-white border-0 focus:ring-none focus:outline-none"
              disabled={isLoading}
            />

            {error && (
              <p className="text-red-400 text-sm text-center">{error}</p>
            )}

            <button
              type="submit"
              disabled={isLoading || !email || !password}
              className="w-full bg-white text-black py-4 rounded-full font-medium hover:bg-gray-100 transition-colors disabled:opacity-50"
            >
              {isLoading ? 'Signing in...' : 'Continue'}
            </button>
          </form>

          <div id="clerk-captcha" data-clerk-captcha className="mt-2" />

          <p className="text-white/60 text-sm mt-8">
            Don&apos;t have an account?{' '}
            <Link href={signUpHref} className="text-white font-medium hover:underline">
              Sign Up
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
