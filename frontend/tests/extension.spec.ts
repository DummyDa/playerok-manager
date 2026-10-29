import { test, expect, type Page } from "@playwright/test";
import path from "node:path";

const job = { id: "order:transfer", lease: "lease", slug: "LibertyFigure-123", recipient: "buyer_name", recipient_id: 123, stars: 25 };

async function fixture(page: Page, options: { cost?: number; finalPeer?: number; deny?: boolean; ownedFlow?: boolean; menuOpen?: boolean } = {}) {
  await page.clock.install();
  await page.setContent('<div id="Main"></div><div class="Modal"><div class="modal-dialog"><h1>Liberty Figure #123</h1><button id="transfer">Transfer</button></div></div>');
  await page.evaluate(({ options, job }) => {
    const w = window as any; w.messages = []; w.clicks = 0;
    let listener: any;
    w.chrome = { runtime: {
      onMessage: { addListener(fn: any) { listener = fn; w.send = (message: any) => new Promise(resolve => listener(message, {}, resolve)); } },
      async sendMessage(message: any) {
        w.messages.push(message);
        if (message.type === "job-status") return { state: "AUTHORIZED" };
        if (message.event === "submit") return options.deny ? { error: "No permission" } : { permit: true };
        return { accepted: true };
      },
    } };
    function wireTransfer(dialog: Element) {
    (dialog.querySelector("#transfer")! as HTMLElement).onclick = () => {
      dialog.innerHTML = '<input type="search"><div id="results"></div>';
      dialog.querySelector("input")!.oninput = () => {
        document.getElementById("results")!.innerHTML = '<div class="PeerPickerItem"><span data-peer-id="123"></span>@buyer_name</div>';
        (dialog.querySelector(".PeerPickerItem")! as HTMLElement).onclick = () => {
          dialog.innerHTML = `<p>Transfer Liberty Figure #123 to buyer</p><div data-peer-id="${options.finalPeer ?? 123}"></div><button id="final"><i class="icon">&#xe800;</i>Transfer for <span class="icon">&#xe801;</span>${options.cost ?? 25}</button><button>Cancel</button>`;
          document.getElementById("final")!.onclick = () => { w.clicks++; };
        };
      };
    };
    }
    const dialog = document.querySelector(".modal-dialog")!;
    if (!options.ownedFlow) wireTransfer(dialog);
    else {
      dialog.innerHTML = '<h1>Liberty Figure #123</h1><button aria-label="Close">Close</button>';
      (dialog.querySelector("button")! as HTMLElement).onclick = () => dialog.closest(".Modal")!.remove();
      const header = document.createElement("div"); header.id = "LeftMainHeader";
      header.innerHTML = '<button aria-label="Open menu">Menu</button>'; document.body.append(header);
      header.querySelector("button")!.onclick = () => {
        w.menuClicks = (w.menuClicks || 0) + 1;
        const existing = document.querySelector(".MenuItem"); if (existing) { existing.remove(); return; }
        const item = document.createElement("div"); item.className = "MenuItem"; item.setAttribute("aria-label", "Profile"); item.innerHTML = '<i class="icon">&#xe800;</i>My Profile'; document.body.append(item);
        item.onclick = () => {
          item.remove(); const pane = document.createElement("div"); pane.id = "RightColumn";
          pane.innerHTML = '<button class="Tab"><i class="icon">&#xe800;</i>Gifts 2</button>'; document.body.append(pane);
          pane.querySelector("button")!.onclick = () => {
            pane.innerHTML = '<div class="interactive-gift scroll-item">First gift</div><div class="interactive-gift scroll-item">Second gift</div>';
            [...pane.children].forEach((card, index) => {
              (card as HTMLElement).onclick = () => {
                w.openedCards = [...(w.openedCards || []), index];
                const root = document.createElement("div"); root.className = "Modal";
                root.innerHTML = `<div class="modal-dialog"><h1>${index === 0 ? "Other Gift #999" : "Liberty Figure #123"}</h1><button aria-label="Close">Close</button><button id="transfer"><i class="icon">&#xe800;</i>Transfer</button></div>`;
                document.body.append(root);
                (root.querySelector("[aria-label=Close]")! as HTMLElement).onclick = () => root.remove();
                wireTransfer(root.querySelector(".modal-dialog")!);
              };
            });
          };
        };
      };
      if (options.menuOpen) header.querySelector("button")!.click();
    }
    w.job = job;
  }, { options, job });
  await page.addScriptTag({ path: path.resolve("../extension/content.js") });
  await page.evaluate(async () => { const w = window as any; await w.send({ type: "prepare", job: w.job }); });
}

test("WebA adapter selects exact NFT and recipient, consumes a permit and clicks once", async ({ page }) => {
  await fixture(page);
  await page.clock.runFor(1500);
  await expect.poll(() => page.evaluate(() => (window as any).clicks)).toBe(1);
  const messages = await page.evaluate(() => (window as any).messages);
  expect(messages.filter((m: any) => m.event === "submit")).toHaveLength(1);
  expect(messages.find((m: any) => m.event === "prepared")).toMatchObject({ slug: job.slug, recipient_id: 123, stars: 25 });
  expect(messages.at(-1).event).toBe("done");
});

test("public gift card is closed and the exact owned NFT is selected through My Profile", async ({ page }) => {
  await fixture(page, { ownedFlow: true });
  await page.clock.runFor(2000);
  await expect.poll(() => page.evaluate(() => (window as any).clicks)).toBe(1);
  expect(await page.evaluate(() => (window as any).openedCards)).toEqual([0, 1]);
  const messages = await page.evaluate(() => (window as any).messages);
  expect(messages.filter((m: any) => m.event === "submit")).toHaveLength(1);
  expect(messages.find((m: any) => m.event === "prepared").slug).toBe(job.slug);
});

test("an already open Telegram menu is used without toggling it closed", async ({ page }) => {
  await fixture(page, { ownedFlow: true, menuOpen: true });
  await page.clock.runFor(2000);
  await expect.poll(() => page.evaluate(() => (window as any).clicks)).toBe(1);
  expect(await page.evaluate(() => (window as any).menuClicks)).toBe(1);
});

test("changed Stars price stops before requesting payment permission", async ({ page }) => {
  await fixture(page, { cost: 26 });
  await page.clock.runFor(1500);
  const messages = await page.evaluate(() => (window as any).messages);
  expect(messages.some((m: any) => m.event === "error")).toBe(true);
  expect(messages.some((m: any) => m.event === "submit")).toBe(false);
  expect(await page.evaluate(() => (window as any).clicks)).toBe(0);
});

test("wrong final recipient never receives a gift", async ({ page }) => {
  await fixture(page, { finalPeer: 999 });
  await page.clock.runFor(27000);
  expect(await page.evaluate(() => (window as any).clicks)).toBe(0);
  expect(await page.evaluate(() => (window as any).messages.some((m: any) => m.event === "submit"))).toBe(false);
});

test("a revoked permission leaves the confirmation button untouched", async ({ page }) => {
  await fixture(page, { deny: true });
  await page.clock.runFor(1500);
  expect(await page.evaluate(() => (window as any).clicks)).toBe(0);
  expect(await page.evaluate(() => (window as any).messages.some((m: any) => m.event === "done"))).toBe(false);
});

test("real keyboard interaction does not cancel preparation", async ({ page }) => {
  await fixture(page);
  await page.keyboard.press("ArrowDown");
  await page.clock.runFor(1500);
  await expect.poll(() => page.evaluate(() => (window as any).clicks)).toBe(1);
  expect(await page.evaluate(() => (window as any).messages.some((m: any) => m.event === "done"))).toBe(true);
});

for (const variant of ["authorized", "login", "loading", "inactive", "sidebar"]) {
  test(`WebA readiness recognizes ${variant} without treating hidden login markup as logged out`, async ({ page }) => {
    await page.setContent(variant === "authorized" ? '<div id="Main" style="height:500px">Chats</div><div class="Auth" style="display:none">Login</div>' : variant === "sidebar" ? '<div id="LeftColumn-main" style="height:500px">Chats</div>' : variant === "inactive" ? '<div id="AppInactive" style="height:500px">Please reload Telegram</div>' : variant === "login" ? '<div class="Auth" style="height:500px">Login</div>' : '<div>Loading</div>');
    await page.evaluate(() => {
      const w = window as any;
      w.chrome = { runtime: { onMessage: { addListener(fn: any) { w.probe = () => new Promise(resolve => fn({ type: "probe" }, {}, resolve)); } } } };
    });
    await page.addScriptTag({ path: path.resolve("../extension/content.js") });
    const probe = await page.evaluate(() => (window as any).probe());
    expect(probe.ready).toBe(["authorized","sidebar"].includes(variant));
    if (variant === "inactive") expect(probe.needsReload).toBe(true);
    if (variant === "loading") expect(probe.error).not.toContain("Войдите");
    if (variant === "login") expect(probe.error).toContain("Войдите");
  });
}
