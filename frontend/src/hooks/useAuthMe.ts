import { useQuery } from "@tanstack/react-query";
import { fetchAuthMe } from "../services/authService";

export function useAuthMe() {
  return useQuery({
    queryKey: ["auth-me"],
    queryFn: ({ signal }) => fetchAuthMe(signal),
    staleTime: 30_000,
    retry: false,
  });
}
