'use client'

import { useState } from 'react'
import { useSignUp } from '@clerk/nextjs'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'

export default function CustomSignUp() {
  const { isLoaded, signUp, setActive } = useSignUp()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [verificationCode, setVerificationCode] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState('')
  const [needsVerification, setNeedsVerification] = useState(false)
  const router = useRouter()
  const searchParams = useSearchParams()
  const redirectParam = searchParams.get('redirect_url')
  const redirectTarget =
    redirectParam && redirectParam.startsWith('/') ? redirectParam : '/onboarding'
  const signInHref = redirectParam
    ? `/sign-in?redirect_url=${encodeURIComponent(redirectParam)}`
    : '/sign-in'

  const handleGoogleSignUp = async () => {
    if (!isLoaded) return

    setError('')
    setIsLoading(true)
    try {
      await signUp.authenticateWithRedirect({
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

  const handleEmailVerification = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!isLoaded || !verificationCode) return

    setIsLoading(true)
    setError('')

    try {
      console.log('Attempting email verification...')
      const result = await signUp.attemptEmailAddressVerification({
        code: verificationCode
      })

      console.log('Verification result:', result)

      if (result.status === 'complete') {
        console.log('Verification complete, setting active session...')
        await setActive({ session: result.createdSessionId })
        console.log('Redirecting to onboarding...')
        router.push(redirectTarget)
      }
    } catch (err: unknown) {
      console.error('Verification error:', err)
      const error = err as { errors?: Array<{ message: string }> }
      setError(error.errors?.[0]?.message || 'Invalid verification code')
    } finally {
      setIsLoading(false)
    }
  }

  const handleEmailSignUp = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!isLoaded || !email || !password) return

    setIsLoading(true)
    setError('')

    try {
      console.log('Starting sign-up process...')
      const result = await signUp.create({
        emailAddress: email,
        password
      })

      console.log('Sign-up result:', result)
      console.log('Sign-up status:', result.status)
      console.log('Required fields:', result.requiredFields)
      console.log('Optional fields:', result.optionalFields)

      if (result.status === 'complete') {
        console.log('Sign-up complete, setting active session...')
        await setActive({ session: result.createdSessionId })
        console.log('Redirecting to onboarding...')
        router.push(redirectTarget)
      } else if (result.status === 'missing_requirements' && result.unverifiedFields?.includes('email_address')) {
        // Handle email verification requirement
        console.log('Email verification required, preparing verification...')
        await result.prepareEmailAddressVerification({ strategy: 'email_code' })
        setNeedsVerification(true)
        setError('')
      } else {
        // Handle other cases - this shouldn't happen with your previous working setup
        console.log('Unexpected sign-up status:', result.status)
        console.log('Missing fields:', result.missingFields)
        console.log('Unverified fields:', result.unverifiedFields)
        setError('Sign-up incomplete. Please check the console for details.')
      }
    } catch (err: unknown) {
      console.error('Sign-up error:', err)
      const error = err as { errors?: Array<{ message: string }> }
      setError(error.errors?.[0]?.message || 'Failed to create account')
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
            Create your account
          </h2>
          <p className="text-white/60 text-sm font-book">
            Fill in the details to get started.
          </p>
        </div>

        <div className="w-full space-y-6">
          <button
            onClick={handleGoogleSignUp}
            disabled={isLoading || !isLoaded}
            className="w-full bg-white text-black py-4 rounded-full font-medium hover:bg-white/80 transition-colors flex items-center justify-center space-x-2 disabled:opacity-50"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24">
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

          {!needsVerification ? (
            <form onSubmit={handleEmailSignUp} className="w-full space-y-4">
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
                {isLoading ? 'Creating account...' : 'Continue'}
              </button>
            </form>
          ) : (
            <form onSubmit={handleEmailVerification} className="w-full space-y-4">
              <p className="text-white text-center mb-4">
                We sent a verification code to <span className="font-medium">{email}</span>
              </p>

              <input
                type="text"
                placeholder="Enter verification code"
                value={verificationCode}
                onChange={(e) => setVerificationCode(e.target.value)}
                className="w-full bg-[#3C3C3C] text-white px-5 py-4 rounded-full placeholder-white border-0 focus:ring-none focus:outline-none text-center"
                disabled={isLoading}
              />

              {error && (
                <p className="text-red-400 text-sm text-center">{error}</p>
              )}

              <button
                type="submit"
                disabled={isLoading || !verificationCode}
                className="w-full bg-white text-black py-4 rounded-full font-medium hover:bg-gray-100 transition-colors disabled:opacity-50"
              >
                {isLoading ? 'Verifying...' : 'Verify Email'}
              </button>

              <button
                type="button"
                onClick={() => setNeedsVerification(false)}
                className="w-full text-white/60 text-sm hover:text-white"
              >
                Back to sign up
              </button>
            </form>
          )}

          <div id="clerk-captcha" data-clerk-captcha className="mt-2" />

          <p className="text-white/60 text-sm mt-8">
            Already have an account?{' '}
            <Link href={signInHref} className="text-white font-medium hover:underline">
              Sign In
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
