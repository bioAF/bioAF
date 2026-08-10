import { redirect } from "next/navigation";

// Notification settings moved into the tabbed Profile page. Keep this route as a
// redirect so existing links and bookmarks continue to work.
export default function NotificationsRedirect() {
  redirect("/profile?tab=notifications");
}
