import "popper";
// We cannot import from bootstrap, only the whole script (see Debian bug #1104445)
import "bootstrap";

/**
 * Generate a self-closed notification using Bootstrap toasts
 *
 * :param message: The message to show: it can be a string or an HTML element.
 * :param close_button: Whether to show a close button
 * :param delay: Delay in milliseconds after which the notification disappears.
 */
export function toast_notify({message, close_button=true, delay=2000})
{
    let main_toast_container = document.getElementById("user-message-container").children[0];

    let toast = document.createElement("div");
    toast.classList.add("toast", "align-items-center");
    toast.attributes["role"] = "alert";
    toast.attributes["aria-live"] = "assertive";
    toast.attributes["aria-atomic"] = true;

    let toast_body = document.createElement("div");
    toast_body.classList.add("toast-body");

    if (typeof(message) == "string")
        toast_body.innerText = message;
    else
        toast_body.append(message);

    if (close_button)
    {
        let dflex = document.createElement("div");
        dflex.classList.add("d-flex");
        toast.append(dflex);
        dflex.append(toast_body);

        let close_button = document.createElement("button");
        close_button.classList.add("btn-close", "me-2", "m-auto");
        close_button.attributes["type"] = "button";
        close_button.attributes["data-bs-dismiss"] = "toast";
        close_button.attributes["aria-label"] = "Close";
        dflex.append(close_button);
    } else {
        toast.append(toast_body);
    }

    main_toast_container.append(toast);

    toast.addEventListener("hidden.bs.toast", () => {
        toast.remove();
    });

    let bs_toast = new bootstrap.Toast(toast, {
        autohide: true,
        delay: delay,
    });
    bs_toast.show();
}

/// Base class for Debusine DOM widgets
export class DebusineWidget
{
    constructor(element)
    {
        this.element = element;
    }

    /// Set up the widget on the given DOM element
    static setup(element)
    {
        // Make sure element.debusine exists
        const debusine_data = element.debusine;
        if (debusine_data === undefined)
            element.debusine = {};

        // Stop if the element is already setup as this widget
        const data_key = this.name;
        if (element.debusine[data_key])
            return;

        let widget;
        try {
            widget = new this(element);
        } catch (e) {
            console.error("%s: widget constructor failed on %o: %o", this.name, element, e);
            return;
        }

        element.debusine[data_key] = widget;
    }

    /// Set up DebusineRemoteDropdown on all elements matching the given selector
    static setup_all(selector)
    {
        if (!selector)
            selector = this.default_selector;
        if (!selector)
            console.error("setup_all called for default selector but class %s does not define default_selector", this.name);
        for (const element of document.querySelectorAll(selector))
            this.setup(element);
    }
}

/// Remotely toggle a Bootstrap dropdown menu
export class DebusineRemoteDropdown extends DebusineWidget
{
    static default_selector = ".debusine-dropdown-remote";

    constructor(element)
    {
        super(element);
        const target_name = this.element.dataset.bsTarget;
        if (!target_name)
            throw new Error(`DebusineRemoteDropdown element found without data-bs-target attribute`);
        this.target = document.querySelector(target_name);
        if (!this.target)
            throw new Error(`${element.dataset.bsTarget} not found as DebusineRemoteDropdown target`);

        // Look for a parent dropdown we may want to close
        this.parent_dropdown = null;
        const containing_dropdown = this.element.closest(".dropdown-menu")
        if (containing_dropdown)
            this.parent_dropdown = containing_dropdown.parentElement.querySelector("*[data-bs-toggle='dropdown']");

        // Install the click handler on the remote control element
        this.element.addEventListener("click", evt => {
            evt.stopPropagation();
            this.activate(evt);
        });
    }

    /// Activate the remote
    activate(evt)
    {
        // If we are inside another dropdown, close it
        if (this.parent_dropdown)
        {
            const parent_dropdown = bootstrap.Dropdown.getInstance(this.parent_dropdown);
            parent_dropdown.hide();
        }

        // Dropdown that we control
        const dropdown = bootstrap.Dropdown.getOrCreateInstance(this.target);
        dropdown.toggle();
    }
}

/// Close a containing dropdown menu
export class DebusineCloseDropdown extends DebusineWidget
{
    static default_selector = ".debusine-dropdown-close";

    constructor(element)
    {
        super(element);

        // Look for a parent dropdown we may want to close
        this.target = null;
        const containing_dropdown = this.element.closest(".dropdown-menu")
        if (containing_dropdown)
            this.target = containing_dropdown.parentElement.querySelector("*[data-bs-toggle='dropdown']");

        if (!this.target)
            throw new Error("DebusineCloseDropdown element is not inside a dropdown menu");

        // Install the click handler on the remote control element
        this.element.addEventListener("click", evt => {
            evt.stopPropagation();
            this.activate(evt);
        });
    }

    /// Activate the remote
    activate(evt)
    {
        const dropdown = bootstrap.Dropdown.getOrCreateInstance(this.target);
        dropdown.hide();
    }
}
