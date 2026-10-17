"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";
import { ApiError } from "./api";

// Sends the user to the login page when the backend session is missing or expired.
export function useAuthGuard() {
  const router = useRouter();
  return useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        router.replace("/login");
        return true;
      }
      return false;
    },
    [router],
  );
}
