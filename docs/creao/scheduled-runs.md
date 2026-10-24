# Scheduled Runs

Source: https://docs.creao.ai/features/scheduled-runs

## Overview

Scheduled Runs let you execute agents automatically on a recurring basis. Set up a schedule once, and the super agent runs it at the specified intervals — delivering results without manual intervention.

Scheduled runs require **Pro Plus or higher**. On Free or Pro, existing schedules stay saved (and can be paused or deleted) but don’t execute until you upgrade. Pro Plus, Max, and Ultra also each have their own limit on how many schedules can run at the same time — upgrading raises that limit.

## Setting up a schedule

1

Open an agent

Navigate to the **Agents** page and select the agent you want to schedule.

The Agents page listing your available agents

2

Configure the schedule

Click the **Schedule** tab on the agent detail page, then click **+ Create Schedule**.

The Schedule tab with the option to create a new schedule

Configure your schedule settings:

- **Frequency** — how often the agent runs (interval-based or custom cron expression)
- **Time** — when during the day the run executes
- **Timezone** — the timezone for scheduling
- **Delivery** — choose whether to receive an email summary after each run (see [Email delivery](scheduled-runs.mdx#email-delivery) below)

A schedule runs on the agent’s **Default model** (set once in the agent sidebar) rather than a model chosen per schedule — see [Default model](https://docs.creao.ai/features/agents#default-model).

The schedule configuration dialog with interval and cron options

Runs must be spaced at least 5 minutes apart. This applies to interval schedules and custom cron expressions alike — if your configuration would fire more often, you’ll see a validation message before you can save it.

3

Activate

Click **Create Schedule** to activate. The next run time appears in the agent detail page.

## Schedule options

| Frequency | Description | Example |
| --- | --- | --- |
| **Daily** | Runs once every day at the specified time | Daily standup summary at 9:00 AM |
| **Weekly** | Runs once per week on the specified day | Weekly analytics report every Monday |
| **Monthly** | Runs once per month on the specified date | Monthly invoice generation on the 1st |
| **Custom** | Cron expression for advanced scheduling | Every weekday at 6:00 PM |

## Email delivery

You can opt in to receive an email summary after each scheduled run. When email delivery is enabled, you get a formatted message with:

- **Run outcome** — whether the run succeeded, failed, or was skipped
- **Result summary** — a concise summary of what the agent produced
- **Session link** — a direct link to the full session for details

To enable it, set **Delivery** to **Email** when creating or editing a schedule. You can change the delivery setting at any time from the Schedule tab.

Email delivery is best-effort — if the summary email fails to send, the run itself is not affected.

## Run history

Each scheduled execution creates a session entry in the agent’s **Sessions** tab. You can:

- **View output** — see what the agent produced for each run
- **Check status** — confirm the run completed successfully or see a **Skipped** badge if the run was skipped due to insufficient credits
- **Review timing** — see exactly when each run executed
- **Delivery status** — see whether the result email was sent successfully (when email delivery is enabled)

The Sessions tab shows the history of all scheduled and manual runs

### Following up on a run

Every scheduled run happens in its own chat thread. These threads stay out of your recent-threads sidebar so scheduled agents don’t crowd out your own conversations — you’ll find them through the agent’s Sessions tab or the run’s session link.

The moment you send a follow-up message in a scheduled run’s thread, it becomes part of your recent work: it appears in the sidebar like any other conversation and stays there. Searching in the command palette (Cmd+K) always finds scheduled threads, whether or not you’ve followed up.

## Managing schedules

### Pausing a schedule

Toggle the schedule off to pause it. The configuration is preserved — toggle it back on to resume.

### Modifying inputs

Update the default inputs for scheduled runs at any time. Changes take effect on the next scheduled execution.

### Deleting a schedule

Remove the schedule entirely from the agent detail page. Past run history is preserved.

Scheduled runs consume credits just like manual runs, with a minimum charge of 0.1 credits per completed run even if actual usage is lower. Skipped, deferred, and cancelled runs are not charged. Monitor your usage on the billing page to avoid unexpected costs.

## Skipped runs and auto-pause

If your account runs out of credits, scheduled runs are **skipped** instead of failing. Skipped runs show a **Skipped** badge in the Sessions tab and count toward the auto-pause threshold.

When a schedule accumulates **2 consecutive skipped runs**, it is automatically paused and you receive an email explaining:

- Why the schedule was paused (insufficient credits)
- How to resume (add credits and re-enable the schedule)

You can re-enable a paused schedule at any time from the Schedule tab. The auto-pause counter resets when you manually re-enable.

Skipped runs auto-pause faster than other failures (2 consecutive skips vs 5 for general errors) to avoid silently burning through daily credit grants on schedules that cannot succeed.

## Use cases

## Daily reports

Generate a morning briefing from your email, calendar, and project management tools.

## Weekly summaries

Compile analytics data into a formatted report every Monday.

## Data monitoring

Check external data sources on a schedule and surface anomalies.

## Content generation

Produce recurring content like social media posts or newsletter drafts.
