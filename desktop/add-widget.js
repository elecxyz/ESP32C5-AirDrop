var target = null;
var alreadyPresent = false;
var ps = panels();
for (var i = 0; i < ps.length; ++i) {
    var widgets = ps[i].widgets();
    for (var j = 0; j < widgets.length; ++j) {
        if (widgets[j].type === "org.esp32c5.airdrop") alreadyPresent = true;
        if (!target && widgets[j].type === "org.kde.plasma.icontasks") target = ps[i];
    }
}
if (!alreadyPresent) {
    if (!target && ps.length > 0) target = ps[0];
    if (target) target.addWidget("org.esp32c5.airdrop");
}
