import { notFound } from "next/navigation";
import { SharedRouteBookView } from "@/components/shared-routebook";
import { getSharedRouteBook } from "@/lib/api";

export default async function SharePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const routebook = await getSharedRouteBook(token);
  if (!routebook) notFound();
  return <SharedRouteBookView routebook={routebook} />;
}
