class OpenProjects {
    constructor(lst) {
        this.open_projects = lst;
    }

    read() {
        let projects = document.querySelectorAll(".repo");
        for (let project of projects) {
            let name = project.querySelector("kbd.name").textContent;
            project.open = this.open_projects.indexOf(name) >= 0;
        }
    }

    write() {
        this.open_projects.length = 0;
        let projects = document.querySelectorAll(".repo");
        for (let project of projects) {
            let name = project.querySelector("kbd.name").textContent;
            if (project.open) {
                this.open_projects.push(name);
            }
        }
    }

    static load() {
        if (window.localStorage["open-projects"]) {
            return new OpenProjects(window.localStorage["open-projects"].split(" "));
        } else {
            return new OpenProjects([]);
        }
    }

    save() {
        window.localStorage["open-projects"] = this.open_projects.join(" ");
    }
}

let OPEN_PROJECTS = null;

function init() {
    OPEN_PROJECTS = OpenProjects.load();
    OPEN_PROJECTS.read();

    let projects = document.querySelectorAll(".repo");
    for (let project of projects) {
        project.addEventListener("toggle", () => {
            OPEN_PROJECTS.write();
            OPEN_PROJECTS.save();
        });
    }
};

requestAnimationFrame(init);
