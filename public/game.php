<?php
require_once __DIR__ . '/db_login.php';
if (($_GET['guest'] ?? '') === 'true') {
    session_regenerate_id(true);
    $_SESSION = ['username' => 'Guest', 'guest' => true];
}
if (!isset($_SESSION['accountID']) && empty($_SESSION['guest'])) {
    redirect_to('user_login_menu.php');
}
?>

<!DOCTYPE html>
<html lang="en">
<head>
    <link rel="stylesheet" href="styles.css">
<meta charset="UTF-8">
<title>Rock Paper Scissors</title>
</head>

<body>
    <h1>
        Machine Learning Rock Paper Scissors
    </h1>
    <h2>
        The Game
    </h2> <br>
    <p>Welcome <?= escape_html($_SESSION['username']) ?>!</p>
</body>

</html>

