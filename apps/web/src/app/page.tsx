import { RouteBookWorkspace } from "@/components/routebook-workspace";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ routebook?: string }>;
}) {
  const { routebook } = await searchParams;
  return <RouteBookWorkspace initialRouteBookId={routebook ?? null} />;
}
