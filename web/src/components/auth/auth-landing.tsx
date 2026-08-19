import Link from 'next/link'

export default function AuthLanding() {
  return (
    <div className="min-h-screen bg-black flex items-center justify-center">
      <div className="flex flex-col items-center text-center px-6 w-full max-w-sm">
        <div className="mb-16">
          <h1 className="text-white text-4xl mb-4 font-normal">
            Personality<br />Machine
          </h1>
          <p className="text-white/60 text-base">
            Build your own companion
          </p>
        </div>

        <div className="w-full space-y-4">
          <Link
            href="/sign-in"
            className="block w-full bg-white text-black py-4 rounded-full text-center font-medium hover:bg-white/80 transition-colors"
          >
            Sign In
          </Link>

          <Link
            href="/sign-up"
            className="block w-full bg-[#3C3C3C] text-white py-4 rounded-full text-center font-medium hover:bg-[#2C2C2C] transition-colors"
          >
            Sign Up
          </Link>
        </div>
      </div>
    </div>
  )
}
