# OpenHands Frontend Mission: Bluestaq Notes REST API Demonstration

## 1. Mission

Build a minimal, polished React/TypeScript/Vite frontend that demonstrates and exercises the backend Notes REST service developed under the Bluestaq backend mission.

The frontend is a functional demonstration and integration-testing surface for the backend architecture.

It must demonstrate:

- Multiple users.
- Note creation, retrieval, updating, and deletion.
- Private note ownership.
- Sharing notes with other users.
- READ and WRITE permissions.
- Permission revocation.
- Optimistic concurrency.
- Authentication context and demo identity.
- Backend-enforced authorization.
- Error handling and validation.

The frontend must work end-to-end against the actual backend implementation.

The backend is authoritative for all domain rules, permissions, persistence, and security decisions.

Do not duplicate business logic in the frontend.

## 2. Mandatory first step: inspect the backend

Before implementing the frontend:

1. Read INSTRUCTIONS_BACKEND.md, OPENHANDS_SPEC.md, and the backend architecture documentation if available.
2. Inspect the actual backend implementation.
3. Inspect the FastAPI OpenAPI specification.
4. Identify the actual request and response schemas.
5. Identify the authentication mechanism and demo identity configuration.
6. Identify the exact error response structure and status codes.
7. Identify the actual concurrency mechanism.
8. Verify all available endpoints.

Do not invent API endpoints, fields, response properties, or error formats.

The actual backend contract is authoritative.

If the backend does not implement a capability, do not silently simulate it in the frontend. Document the missing capability and coordinate a backend change only if it is necessary for the assignment.

Create or update docs/FRONTEND_INTEGRATION.md documenting the actual API contract and integration assumptions.

## 3. Technology stack

Frontend:

- React 18+
- TypeScript
- Vite
- React Router only if useful.
- Vitest.
- React Testing Library.
- CSS modules or simple organized CSS.
- Fetch API.

Keep dependencies minimal.

Do not introduce a large UI component framework.

Do not introduce unnecessary state-management libraries. A centralized React hook is sufficient for this application.

## 4. Frontend architecture

The frontend must follow this architectural rule:

COMPONENTS DO NOT CALL FETCH AND DO NOT KNOW API URLS.

Use this structure:

frontend/
src/
api/
client.ts
types.ts
errors.ts
hooks/
useWorkspace.ts
components/
AppHeader.tsx
UserBadge.tsx
UserSwitcher.tsx
NoteList.tsx
NoteEditor.tsx
ShareDialog.tsx
LoadingState.tsx
EmptyState.tsx
ErrorMessage.tsx
ConfirmDialog.tsx
lib/
formatDate.ts
noteDisplayTitle.ts
test/
FakeApiClient.ts
App.tsx
main.tsx

Responsibilities:

### api/client.ts

The only module responsible for HTTP communication.

Requirements:

- Typed methods.
- One method per backend endpoint used by the frontend.
- Centralized base URL.
- Centralized headers.
- Demo identity header support.
- Consistent response handling.
- Consistent error conversion.
- No React imports.
- No component-specific behavior.

The client must not contain business authorization rules.

### api/types.ts

Define TypeScript interfaces corresponding to actual backend API schemas.

Do not independently redefine backend domain behavior.

Keep wire types separate from frontend view models where necessary.

### hooks/useWorkspace.ts

Owns:

- Current user.
- Available demo users.
- Notes.
- Selected note.
- Current view (My Notes / Shared With Me).
- Loading state.
- Error state.
- Validation state.
- Note creation and mutation.
- Sharing and revocation.
- Refresh and selection behavior.

This is the only application layer that communicates with the API client.

Components receive data and callbacks through props.

### Components

Components are presentation-oriented.

They must not:

- Call fetch.
- Know API endpoint URLs.
- Implement authorization decisions.
- Access persistence.
- Implement backend business rules.

The UI may hide or disable actions based on known permissions for usability, but the backend remains authoritative.

## 5. Authentication and demo identity

There is no login screen.

The backend uses its development-only DemoIdentityProvider.

The frontend must visibly identify the active demo user.

Implement a simple Demo User Selector.

Requirements:

- Retrieve available users using the backend's actual user endpoint.
- Resolve the current identity through GET /api/v1/me.
- Select an existing seeded user.
- Send the selected user identifier through the exact development-only header supported by the backend, expected to be X-Demo-User-ID.
- Refresh the current user and notes after switching identities.
- Clearly label the interface as DEMO MODE.
- Display the active user's name and email.

The selector must never create users or bypass backend authentication.

The backend must validate the selected identity and independently enforce authorization.

The selector is only available when the backend explicitly enables demo identity selection.

Never present demo identity selection as production authentication.

If the backend is running in production mode or demo identity selection is disabled, do not render the selector.

## 6. Phase F1 — Core Notes application

### Goal

Deliver a working Notes interface against the actual backend.

### Layout

Use a simple two-column application layout.

Left sidebar:

- My Notes.
- Shared With Me.
- Create Note button.
- Active user information.

Main workspace:

- Notes list.
- Note editor.
- Sharing controls when the current user owns the selected note.

Header:

- Application name.
- DEMO MODE indicator when applicable.
- Current user selector.
- Current user display.

Keep the UI clean and professional.

### NoteList

Display:

- Note title.
- Last updated date.
- Owner or shared indicator.
- READ or WRITE permission indicator where appropriate.

Selecting a note loads it into the editor.

Provide a meaningful empty state.

### NoteEditor

Support:

- Create note.
- View note.
- Update note.
- Delete note.
- Title.
- Content.
- Save state.
- Version information.

Behavior:

- Disable editing for READ-only users.
- Hide or disable deletion for non-owners.
- Show sharing controls only to owners.
- Indicate when a note is shared.
- Show the current permission level.
- Do not allow silent overwrites.

Implement the actual concurrency contract used by the backend.

If the backend uses a version field, send the expected version.

If the backend uses ETag/If-Match, preserve and send the appropriate header.

When the backend returns HTTP 409:

- Preserve the user's unsaved content.
- Display a clear conflict message.
- Offer a way to reload the latest version.
- Do not silently overwrite the server version.

### ShareDialog

Allow owners to:

- Retrieve eligible users.
- Select another user.
- Grant READ permission.
- Grant WRITE permission.
- View existing permissions.
- Revoke access.

Display the current permission list.

Prevent sharing with the note owner if the backend disallows it.

Handle duplicate grants and invalid users using actual API error responses.

After granting or revoking permissions, refresh the relevant note and sharing information.

The backend must independently enforce all permissions.

### Phase F1 acceptance criteria

- The application starts successfully.
- The current demo identity is displayed.
- Multiple seeded users can be selected when demo mode is enabled.
- User A can create a private note.
- User B cannot see User A's private note.
- User A can share a note with User B as READ.
- User B can see and retrieve the shared note.
- User B cannot modify the READ-only note.
- User A can change the permission to WRITE.
- User B can modify the shared note.
- User B cannot delete the note or manage its permissions.
- User A can revoke access.
- User B immediately loses access after revocation.
- A stale update displays a conflict rather than overwriting newer content.
- Loading, empty, validation, and error states are implemented.

Do not begin optional features until F1 passes.

## 7. Phase F2 — Integration and usability hardening

### Goal

Make the application a reliable demonstration of backend correctness.

Implement:

- Consistent loading indicators.
- Consistent error presentation.
- Field-level validation errors where supported by the backend response.
- Confirmation before destructive actions.
- Save progress and success indicators.
- Disabled controls during pending mutations.
- Prevention of duplicate submissions.
- Graceful handling of expired or invalid authentication context.
- Graceful handling of inaccessible or deleted notes.
- Correct refresh after permission changes.
- Responsive layout.

### Error handling

Create a centralized API error model.

Preserve:

- HTTP status.
- Backend error code if present.
- Human-readable message.
- Field validation details where available.
- Request correlation ID if provided by the backend.

Do not assume the backend uses a particular error envelope until verified.

Never replace useful backend errors with generic messages.

### User switching

When switching demo users:

- Clear the currently selected note.
- Clear stale user-specific UI state.
- Refresh current-user information.
- Reload the notes listing.
- Reload sharing information when a note is selected.
- Do not allow data from the previous identity to remain visible accidentally.

### Acceptance criteria

- No stale note data appears after user switching.
- No duplicate requests caused by repeated clicks.
- Errors are understandable and recoverable.
- Invalid form submissions show appropriate validation.
- Unauthorized operations show appropriate backend responses.
- No UI action is treated as proof of authorization.

## 8. Phase F3 — Automated frontend testing

### Goal

Test the UI independently of the network and verify actual integration separately.

Use Vitest and React Testing Library.

Create a FakeApiClient implementing the same interface as the real API client.

All component and hook tests must use the fake client.

Never make network requests in ordinary component tests.

Test:

1. Notes list rendering.
2. Owned versus shared indicators.
3. Empty states.
4. Loading states.
5. Error states.
6. Note creation.
7. Note editing.
8. Note deletion.
9. READ-only controls.
10. WRITE controls.
11. Sharing dialog.
12. Permission grant.
13. Permission revocation.
14. User switching.
15. Stale update conflict handling.
16. Validation error rendering.
17. Missing or unexpected response fields.

The fake client must reflect the actual API contract and error behavior.

Do not make the fake client more permissive than the backend.

## 9. Backend integration testing

In addition to fake-client tests, provide a separate integration test suite that exercises the actual running backend.

Use an integration test configuration or browser automation where practical.

Demonstrate this workflow:

1. Start the backend with seeded demo users.
2. Start the frontend.
3. Select User A.
4. Create a private note.
5. Select User B.
6. Verify the note is not visible.
7. Select User A.
8. Share the note with User B using READ.
9. Select User B.
10. Verify the note is visible and read-only.
11. Attempt to update through the UI.
12. Verify the UI prevents the operation.
13. Attempt an unauthorized API operation directly if practical.
14. Verify the backend rejects it.
15. Change the permission to WRITE as User A.
16. Verify User B can edit.
17. Attempt to delete as User B and verify rejection.
18. Revoke access as User A.
19. Verify User B loses access.

The integration suite must verify backend enforcement independently of frontend controls.

## 10. Phase F4 — Optional visual polish

Only after F1, F2, and F3 pass:

- Refine spacing and typography.
- Improve note-list selection.
- Add subtle ownership and permission indicators.
- Improve empty-state presentation.
- Improve narrow-screen behavior.
- Add keyboard-friendly focus and navigation.
- Add accessible labels and dialog behavior.

Keep the interface restrained and professional.

Do not add features outside the backend contract.

Do not implement folders, trash, starring, Markdown, file imports, or whiteboards unless those capabilities are explicitly added to the backend assignment and approved as in-scope.

## 11. Quality gates

At the end of each phase:

- Run frontend tests.
- Run TypeScript type checking.
- Run ESLint.
- Run Vite production build.
- Fix all failures.
- Verify API contract compatibility.
- Update documentation.

Required commands should be documented and executable.

Use strict TypeScript settings.

Avoid any and unsafe type casts unless justified.

Do not suppress lint or type errors to achieve a green build.

Do not add frontend dependencies without a clear reason.

## 12. Integration documentation

Create docs/FRONTEND_INTEGRATION.md containing:

- Frontend architecture.
- API client design.
- Backend endpoint dependencies.
- Authentication and demo identity handling.
- Error mapping.
- Concurrency behavior.
- How to run the frontend with the backend.
- How to run frontend tests.
- How to run end-to-end integration tests.

Update the root README with frontend startup instructions if the repository includes both applications.

## 13. Final deliverable

Deliver a runnable frontend integrated with the actual backend.

Provide:

- React/TypeScript/Vite application.
- Typed API client.
- Centralized workspace hook.
- Notes list and editor.
- Sharing dialog.
- Demo user display and selection when enabled.
- Frontend unit and component tests.
- Backend integration test workflow.
- FRONTEND_INTEGRATION.md.
- Updated README startup instructions.

Final report must state:

- Implemented frontend functionality.
- Backend endpoints exercised.
- Frontend test counts and actual results.
- Integration test results.
- Build and type-check results.
- Known limitations.

Never report tests as passing unless they were actually executed.

The frontend is complete only when it demonstrates the actual backend's multi-user behavior, permissions, and concurrency correctly.

## 14. Scope discipline

This is a backend architecture demonstration with a frontend verification surface.

The backend remains the source of truth.

Do not add domain capabilities solely to make the frontend more interesting.

Do not implement business rules twice.

Do not create frontend-only mock behavior that hides missing backend functionality.

Prioritize:

1. Accurate backend integration.
2. Correct multi-user behavior.
3. Clear presentation of permissions.
4. Reliable error and concurrency handling.
5. Automated testing.
6. Minimal, polished UI.

The goal is to demonstrate that the backend is correctly architected, secure, testable, and usable through a real client.
