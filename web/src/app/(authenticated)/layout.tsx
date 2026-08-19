import {ReactNode} from 'react';
import {currentUser} from '@clerk/nextjs/server';
import AuthLanding from '@/components/auth/auth-landing';
import {UserProvider} from '@/components/auth/user-provider';
import type {Metadata} from "next";
import AppShell from "@/components/layout/app-shell";

export const metadata: Metadata = {
    title: "Emotion Machine",
    description: "Build your own AI companion",
};

export default async function AuthenticatedLayout({children}: { children: ReactNode }) {
    const user = await currentUser();

    if (!user) {
        return <AuthLanding/>;
    }

    const emUser = {
        id: user.id,
        email: user.primaryEmailAddress?.emailAddress ?? null,
        fullName: user.fullName ?? user.username ?? null,
        imageUrl: user.imageUrl ?? null,
    };

    return (
        <UserProvider user={emUser}>
            <AppShell>
                {children}
            </AppShell>
        </UserProvider>
    );
}
