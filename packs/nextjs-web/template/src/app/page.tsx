import { Page } from "@/components/page";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";

export default function Home() {
  return (
    <Page title="App" description="Replace this page with the product described in docs/spec.md.">
      <Card>
        <CardHeader>
          <CardTitle>Nothing here yet</CardTitle>
          <CardDescription>The first feature will live on this page.</CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState title="No items yet">
            Lists show what goes here and how to add the first one.
          </EmptyState>
        </CardContent>
      </Card>
    </Page>
  );
}
