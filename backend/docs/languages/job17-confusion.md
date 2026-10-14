# Job17 — complete text-screen confusion findings

Measured here on 160 invented paragraphs, 20 per source, three sentences/lines each. Each cell counts compatible verdicts. These are lexical screens, not speech detection. Source naturalness needs a native check. All old six outputs are preserved. English and other old-screen off-diagonal matches are recorded findings, not test failures, following Ali's instruction. New Dutch and Polish checks accept only their own 20 paragraphs on this set; that is not a real-world accuracy claim.

| Actual source (20 each) | en | es | fr | pt | it | de | nl | pl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| en | 20 | 0 | 0 | 0 | 5 | 0 | 0 | 0 |
| es | 20 | 20 | 11 | 4 | 8 | 0 | 0 | 0 |
| fr | 20 | 10 | 20 | 6 | 17 | 0 | 0 | 0 |
| pt | 20 | 14 | 6 | 20 | 9 | 0 | 0 | 0 |
| it | 20 | 9 | 8 | 3 | 20 | 0 | 0 | 0 |
| de | 20 | 0 | 0 | 0 | 0 | 20 | 0 | 0 |
| nl | 20 | 0 | 0 | 0 | 0 | 3 | 20 | 0 |
| pl | 20 | 1 | 1 | 4 | 6 | 0 | 0 | 20 |

Every observed false match is listed below (including all 140 English matches). The test pins this table and the complete text/IDs in job17-confusion.json. No matches have been hidden or dropped.

| Paragraph ID | Actual source | Falsely accepted as | Complete invented paragraph |
| --- | --- | --- | --- |
| en_08 | en | it | The letter is from my sister.<br>She is in a new house.<br>I will visit her. |
| en_09 | en | it | We have a meeting today.<br>It is in our office.<br>They are coming too. |
| en_13 | en | it | I need a ticket.<br>You have the money.<br>We can buy it here. |
| en_14 | en | it | She has a blue coat.<br>It is in her bag.<br>I will bring it to her. |
| en_18 | en | it | We are making a cake.<br>It is for our friend.<br>They will come tonight. |
| es_01 | es | en | Tengo la llave.<br>Tú puedes abrir la puerta.<br>Nosotros estamos aquí. |
| es_01 | es | fr | Tengo la llave.<br>Tú puedes abrir la puerta.<br>Nosotros estamos aquí. |
| es_01 | es | it | Tengo la llave.<br>Tú puedes abrir la puerta.<br>Nosotros estamos aquí. |
| es_02 | es | en | Ella está en la estación.<br>Su tren llega tarde.<br>Ellos nos esperan allí. |
| es_02 | es | fr | Ella está en la estación.<br>Su tren llega tarde.<br>Ellos nos esperan allí. |
| es_03 | es | en | La sopa está en la mesa.<br>Él la hizo para ti.<br>Nosotros podemos comer ahora. |
| es_03 | es | fr | La sopa está en la mesa.<br>Él la hizo para ti.<br>Nosotros podemos comer ahora. |
| es_04 | es | en | Nuestro profesor tiene un mapa.<br>Es para la clase.<br>Tú puedes mirarlo. |
| es_04 | es | fr | Nuestro profesor tiene un mapa.<br>Es para la clase.<br>Tú puedes mirarlo. |
| es_04 | es | pt | Nuestro profesor tiene un mapa.<br>Es para la clase.<br>Tú puedes mirarlo. |
| es_04 | es | it | Nuestro profesor tiene un mapa.<br>Es para la clase.<br>Tú puedes mirarlo. |
| es_05 | es | en | Ellos están en el jardín.<br>Nosotros queremos sentarnos con ellos.<br>Aquí no hay ruido. |
| es_06 | es | en | Yo estaba en la tienda.<br>El pan era fresco.<br>Ella pagó por él. |
| es_06 | es | fr | Yo estaba en la tienda.<br>El pan era fresco.<br>Ella pagó por él. |
| es_07 | es | en | Tú no estás solo.<br>Nosotros estamos contigo.<br>Él puede ayudarnos. |
| es_07 | es | pt | Tú no estás solo.<br>Nosotros estamos contigo.<br>Él puede ayudarnos. |
| es_08 | es | en | La carta es de mi hermana.<br>Ella vive en una casa nueva.<br>Yo voy a visitarla. |
| es_08 | es | fr | La carta es de mi hermana.<br>Ella vive en una casa nueva.<br>Yo voy a visitarla. |
| es_08 | es | pt | La carta es de mi hermana.<br>Ella vive en una casa nueva.<br>Yo voy a visitarla. |
| es_08 | es | it | La carta es de mi hermana.<br>Ella vive en una casa nueva.<br>Yo voy a visitarla. |
| es_09 | es | en | Tenemos una reunión hoy.<br>Es en nuestra oficina.<br>Ellos también vienen. |
| es_10 | es | en | Él tiene una barca.<br>Nosotros podemos ir con él.<br>El agua está tranquila. |
| es_10 | es | it | Él tiene una barca.<br>Nosotros podemos ir con él.<br>El agua está tranquila. |
| es_11 | es | en | La lámpara está en mi mesa.<br>Yo la uso por la noche.<br>Es muy clara. |
| es_11 | es | fr | La lámpara está en mi mesa.<br>Yo la uso por la noche.<br>Es muy clara. |
| es_12 | es | en | Nuestro perro está en el patio.<br>Él nos espera.<br>Nosotros debemos salir. |
| es_13 | es | en | Yo necesito un billete.<br>Tú tienes el dinero.<br>Nosotros podemos comprarlo aquí. |
| es_13 | es | fr | Yo necesito un billete.<br>Tú tienes el dinero.<br>Nosotros podemos comprarlo aquí. |
| es_13 | es | it | Yo necesito un billete.<br>Tú tienes el dinero.<br>Nosotros podemos comprarlo aquí. |
| es_14 | es | en | Ella tiene un abrigo azul.<br>Está en su bolsa.<br>Yo se lo llevaré. |
| es_14 | es | fr | Ella tiene un abrigo azul.<br>Está en su bolsa.<br>Yo se lo llevaré. |
| es_14 | es | it | Ella tiene un abrigo azul.<br>Está en su bolsa.<br>Yo se lo llevaré. |
| es_15 | es | en | Ellos leen el libro.<br>Es sobre nuestra ciudad.<br>Nosotros conocemos al escritor. |
| es_16 | es | en | El autobús está junto al puente.<br>Nosotros llegamos a tiempo.<br>Tú puedes sentarte conmigo. |
| es_16 | es | fr | El autobús está junto al puente.<br>Nosotros llegamos a tiempo.<br>Tú puedes sentarte conmigo. |
| es_16 | es | pt | El autobús está junto al puente.<br>Nosotros llegamos a tiempo.<br>Tú puedes sentarte conmigo. |
| es_16 | es | it | El autobús está junto al puente.<br>Nosotros llegamos a tiempo.<br>Tú puedes sentarte conmigo. |
| es_17 | es | en | Él estuvo aquí antes.<br>Yo no estaba con él.<br>Ella sabe dónde fue. |
| es_18 | es | en | Nosotros hacemos un pastel.<br>Es para un amigo.<br>Ellos vienen esta noche. |
| es_19 | es | en | Tú puedes tomar la silla.<br>Yo estoy en el suelo.<br>Nosotros tenemos espacio. |
| es_19 | es | fr | Tú puedes tomar la silla.<br>Yo estoy en el suelo.<br>Nosotros tenemos espacio. |
| es_19 | es | it | Tú puedes tomar la silla.<br>Yo estoy en el suelo.<br>Nosotros tenemos espacio. |
| es_20 | es | en | La música suena suave.<br>Ella toca para nosotros.<br>Nosotros la escuchamos. |
| fr_01 | fr | en | J'ai la clé.<br>Tu peux ouvrir la porte.<br>Nous sommes ici. |
| fr_01 | fr | es | J'ai la clé.<br>Tu peux ouvrir la porte.<br>Nous sommes ici. |
| fr_01 | fr | it | J'ai la clé.<br>Tu peux ouvrir la porte.<br>Nous sommes ici. |
| fr_02 | fr | en | Elle est à la gare.<br>Son train est en retard.<br>Ils nous attendent là. |
| fr_02 | fr | es | Elle est à la gare.<br>Son train est en retard.<br>Ils nous attendent là. |
| fr_02 | fr | it | Elle est à la gare.<br>Son train est en retard.<br>Ils nous attendent là. |
| fr_03 | fr | en | La soupe est sur la table.<br>Il l'a faite pour toi.<br>Nous pouvons manger maintenant. |
| fr_03 | fr | it | La soupe est sur la table.<br>Il l'a faite pour toi.<br>Nous pouvons manger maintenant. |
| fr_04 | fr | en | Notre professeur a une carte.<br>Elle est pour la classe.<br>Tu peux la regarder. |
| fr_04 | fr | es | Notre professeur a une carte.<br>Elle est pour la classe.<br>Tu peux la regarder. |
| fr_04 | fr | pt | Notre professeur a une carte.<br>Elle est pour la classe.<br>Tu peux la regarder. |
| fr_04 | fr | it | Notre professeur a une carte.<br>Elle est pour la classe.<br>Tu peux la regarder. |
| fr_05 | fr | en | Ils sont dans le jardin.<br>Nous voulons être avec eux.<br>Il n'y a pas de bruit. |
| fr_05 | fr | es | Ils sont dans le jardin.<br>Nous voulons être avec eux.<br>Il n'y a pas de bruit. |
| fr_05 | fr | pt | Ils sont dans le jardin.<br>Nous voulons être avec eux.<br>Il n'y a pas de bruit. |
| fr_05 | fr | it | Ils sont dans le jardin.<br>Nous voulons être avec eux.<br>Il n'y a pas de bruit. |
| fr_06 | fr | en | J'étais dans le magasin.<br>Le pain était frais.<br>Elle l'a payé. |
| fr_06 | fr | it | J'étais dans le magasin.<br>Le pain était frais.<br>Elle l'a payé. |
| fr_07 | fr | en | Tu n'es pas seul.<br>Nous sommes avec toi.<br>Il peut nous aider. |
| fr_07 | fr | it | Tu n'es pas seul.<br>Nous sommes avec toi.<br>Il peut nous aider. |
| fr_08 | fr | en | La lettre est de ma sœur.<br>Elle a une nouvelle maison.<br>Je vais la voir. |
| fr_08 | fr | es | La lettre est de ma sœur.<br>Elle a une nouvelle maison.<br>Je vais la voir. |
| fr_08 | fr | pt | La lettre est de ma sœur.<br>Elle a une nouvelle maison.<br>Je vais la voir. |
| fr_08 | fr | it | La lettre est de ma sœur.<br>Elle a une nouvelle maison.<br>Je vais la voir. |
| fr_09 | fr | en | Nous avons une réunion.<br>Elle est dans notre bureau.<br>Ils viennent aussi. |
| fr_10 | fr | en | Il a un petit bateau.<br>Nous pouvons aller avec lui.<br>L'eau est calme. |
| fr_10 | fr | es | Il a un petit bateau.<br>Nous pouvons aller avec lui.<br>L'eau est calme. |
| fr_10 | fr | it | Il a un petit bateau.<br>Nous pouvons aller avec lui.<br>L'eau est calme. |
| fr_11 | fr | en | La lampe est sur mon bureau.<br>Je l'utilise la nuit.<br>Elle est très claire. |
| fr_12 | fr | en | Notre chien est dans la cour.<br>Il nous attend.<br>Nous devons sortir. |
| fr_12 | fr | it | Notre chien est dans la cour.<br>Il nous attend.<br>Nous devons sortir. |
| fr_13 | fr | en | Je veux un billet.<br>Tu as l'argent.<br>Nous pouvons l'acheter ici. |
| fr_13 | fr | es | Je veux un billet.<br>Tu as l'argent.<br>Nous pouvons l'acheter ici. |
| fr_13 | fr | pt | Je veux un billet.<br>Tu as l'argent.<br>Nous pouvons l'acheter ici. |
| fr_13 | fr | it | Je veux un billet.<br>Tu as l'argent.<br>Nous pouvons l'acheter ici. |
| fr_14 | fr | en | Elle a un manteau bleu.<br>Il est dans son sac.<br>Je vais le lui apporter. |
| fr_14 | fr | es | Elle a un manteau bleu.<br>Il est dans son sac.<br>Je vais le lui apporter. |
| fr_14 | fr | it | Elle a un manteau bleu.<br>Il est dans son sac.<br>Je vais le lui apporter. |
| fr_15 | fr | en | Ils lisent le livre.<br>Il parle de notre ville.<br>Nous connaissons l'auteur. |
| fr_15 | fr | it | Ils lisent le livre.<br>Il parle de notre ville.<br>Nous connaissons l'auteur. |
| fr_16 | fr | en | Le bus est près du pont.<br>Nous sommes à l'heure.<br>Tu peux t'asseoir avec moi. |
| fr_16 | fr | es | Le bus est près du pont.<br>Nous sommes à l'heure.<br>Tu peux t'asseoir avec moi. |
| fr_16 | fr | pt | Le bus est près du pont.<br>Nous sommes à l'heure.<br>Tu peux t'asseoir avec moi. |
| fr_16 | fr | it | Le bus est près du pont.<br>Nous sommes à l'heure.<br>Tu peux t'asseoir avec moi. |
| fr_17 | fr | en | Il était ici avant.<br>Je n'étais pas avec lui.<br>Elle sait où il est allé. |
| fr_17 | fr | it | Il était ici avant.<br>Je n'étais pas avec lui.<br>Elle sait où il est allé. |
| fr_18 | fr | en | Nous faisons un gâteau.<br>Il est pour notre ami.<br>Ils viennent ce soir. |
| fr_18 | fr | it | Nous faisons un gâteau.<br>Il est pour notre ami.<br>Ils viennent ce soir. |
| fr_19 | fr | en | Tu peux prendre la chaise.<br>Je suis par terre.<br>Nous avons de la place. |
| fr_19 | fr | es | Tu peux prendre la chaise.<br>Je suis par terre.<br>Nous avons de la place. |
| fr_19 | fr | pt | Tu peux prendre la chaise.<br>Je suis par terre.<br>Nous avons de la place. |
| fr_19 | fr | it | Tu peux prendre la chaise.<br>Je suis par terre.<br>Nous avons de la place. |
| fr_20 | fr | en | La musique est douce.<br>Elle joue pour nous.<br>Nous l'écoutons. |
| pt_01 | pt | en | Eu tenho a chave.<br>Tu podes abrir a porta.<br>Nós estamos aqui. |
| pt_01 | pt | es | Eu tenho a chave.<br>Tu podes abrir a porta.<br>Nós estamos aqui. |
| pt_01 | pt | fr | Eu tenho a chave.<br>Tu podes abrir a porta.<br>Nós estamos aqui. |
| pt_01 | pt | it | Eu tenho a chave.<br>Tu podes abrir a porta.<br>Nós estamos aqui. |
| pt_02 | pt | en | Ela está na estação.<br>O seu comboio está atrasado.<br>Eles esperam por nós. |
| pt_02 | pt | es | Ela está na estação.<br>O seu comboio está atrasado.<br>Eles esperam por nós. |
| pt_03 | pt | en | A sopa está na mesa.<br>Ele fez a sopa para ti.<br>Nós podemos comer agora. |
| pt_03 | pt | es | A sopa está na mesa.<br>Ele fez a sopa para ti.<br>Nós podemos comer agora. |
| pt_04 | pt | en | O nosso professor tem um mapa.<br>É para a turma.<br>Tu podes olhar para ele. |
| pt_04 | pt | es | O nosso professor tem um mapa.<br>É para a turma.<br>Tu podes olhar para ele. |
| pt_04 | pt | fr | O nosso professor tem um mapa.<br>É para a turma.<br>Tu podes olhar para ele. |
| pt_04 | pt | it | O nosso professor tem um mapa.<br>É para a turma.<br>Tu podes olhar para ele. |
| pt_05 | pt | en | Eles estão no jardim.<br>Nós queremos ficar com eles.<br>Não há barulho aqui. |
| pt_06 | pt | en | Eu estava na loja.<br>O pão era fresco.<br>Ela pagou por ele. |
| pt_06 | pt | es | Eu estava na loja.<br>O pão era fresco.<br>Ela pagou por ele. |
| pt_07 | pt | en | Tu não estás sozinho.<br>Nós estamos contigo.<br>Ele pode ajudar-nos. |
| pt_08 | pt | en | A carta é da minha irmã.<br>Ela tem uma casa nova.<br>Eu vou visitá-la. |
| pt_08 | pt | es | A carta é da minha irmã.<br>Ela tem uma casa nova.<br>Eu vou visitá-la. |
| pt_08 | pt | fr | A carta é da minha irmã.<br>Ela tem uma casa nova.<br>Eu vou visitá-la. |
| pt_08 | pt | it | A carta é da minha irmã.<br>Ela tem uma casa nova.<br>Eu vou visitá-la. |
| pt_09 | pt | en | Nós temos uma reunião.<br>É no nosso escritório.<br>Eles vêm também. |
| pt_10 | pt | en | Ele tem um barco pequeno.<br>Nós podemos ir com ele.<br>A água está calma. |
| pt_11 | pt | en | A luz está na minha mesa.<br>Eu uso-a à noite.<br>Ela é muito clara. |
| pt_11 | pt | it | A luz está na minha mesa.<br>Eu uso-a à noite.<br>Ela é muito clara. |
| pt_12 | pt | en | O nosso cão está no pátio.<br>Ele espera por nós.<br>Nós devemos sair. |
| pt_12 | pt | es | O nosso cão está no pátio.<br>Ele espera por nós.<br>Nós devemos sair. |
| pt_13 | pt | en | Eu preciso de um bilhete.<br>Tu tens o dinheiro.<br>Nós podemos comprar aqui. |
| pt_13 | pt | es | Eu preciso de um bilhete.<br>Tu tens o dinheiro.<br>Nós podemos comprar aqui. |
| pt_13 | pt | fr | Eu preciso de um bilhete.<br>Tu tens o dinheiro.<br>Nós podemos comprar aqui. |
| pt_13 | pt | it | Eu preciso de um bilhete.<br>Tu tens o dinheiro.<br>Nós podemos comprar aqui. |
| pt_14 | pt | en | Ela tem um casaco azul.<br>Está na sua mala.<br>Eu vou levar o casaco para ela. |
| pt_14 | pt | es | Ela tem um casaco azul.<br>Está na sua mala.<br>Eu vou levar o casaco para ela. |
| pt_15 | pt | en | Eles leem o livro.<br>É sobre a nossa cidade.<br>Nós conhecemos o autor. |
| pt_15 | pt | es | Eles leem o livro.<br>É sobre a nossa cidade.<br>Nós conhecemos o autor. |
| pt_15 | pt | it | Eles leem o livro.<br>É sobre a nossa cidade.<br>Nós conhecemos o autor. |
| pt_16 | pt | en | O autocarro está junto à ponte.<br>Nós estamos a horas.<br>Tu podes sentar-te comigo. |
| pt_16 | pt | es | O autocarro está junto à ponte.<br>Nós estamos a horas.<br>Tu podes sentar-te comigo. |
| pt_16 | pt | fr | O autocarro está junto à ponte.<br>Nós estamos a horas.<br>Tu podes sentar-te comigo. |
| pt_16 | pt | it | O autocarro está junto à ponte.<br>Nós estamos a horas.<br>Tu podes sentar-te comigo. |
| pt_17 | pt | en | Ele esteve aqui antes.<br>Eu não estava com ele.<br>Ela sabe onde ele foi. |
| pt_18 | pt | en | Nós fazemos um bolo.<br>É para o nosso amigo.<br>Eles vêm esta noite. |
| pt_18 | pt | es | Nós fazemos um bolo.<br>É para o nosso amigo.<br>Eles vêm esta noite. |
| pt_18 | pt | it | Nós fazemos um bolo.<br>É para o nosso amigo.<br>Eles vêm esta noite. |
| pt_19 | pt | en | Tu podes levar a cadeira.<br>Eu estou no chão.<br>Nós temos espaço. |
| pt_19 | pt | es | Tu podes levar a cadeira.<br>Eu estou no chão.<br>Nós temos espaço. |
| pt_19 | pt | fr | Tu podes levar a cadeira.<br>Eu estou no chão.<br>Nós temos espaço. |
| pt_19 | pt | it | Tu podes levar a cadeira.<br>Eu estou no chão.<br>Nós temos espaço. |
| pt_20 | pt | en | A música está suave.<br>Ela toca para nós.<br>Nós estamos a ouvir. |
| pt_20 | pt | es | A música está suave.<br>Ela toca para nós.<br>Nós estamos a ouvir. |
| it_01 | it | en | Io ho la chiave.<br>Tu puoi aprire la porta.<br>Noi siamo qui. |
| it_01 | it | es | Io ho la chiave.<br>Tu puoi aprire la porta.<br>Noi siamo qui. |
| it_01 | it | fr | Io ho la chiave.<br>Tu puoi aprire la porta.<br>Noi siamo qui. |
| it_02 | it | en | Lei è alla stazione.<br>Il suo treno è in ritardo.<br>Loro ci aspettano. |
| it_03 | it | en | La zuppa è sulla tavola.<br>Lui l'ha fatta per te.<br>Noi possiamo mangiare ora. |
| it_03 | it | fr | La zuppa è sulla tavola.<br>Lui l'ha fatta per te.<br>Noi possiamo mangiare ora. |
| it_04 | it | en | Il nostro professore ha una mappa.<br>È per la classe.<br>Tu puoi guardarla. |
| it_04 | it | es | Il nostro professore ha una mappa.<br>È per la classe.<br>Tu puoi guardarla. |
| it_04 | it | fr | Il nostro professore ha una mappa.<br>È per la classe.<br>Tu puoi guardarla. |
| it_04 | it | pt | Il nostro professore ha una mappa.<br>È per la classe.<br>Tu puoi guardarla. |
| it_05 | it | en | Loro sono nel giardino.<br>Noi vogliamo stare con loro.<br>Non c'è rumore qui. |
| it_06 | it | en | Io ero nel negozio.<br>Il pane era fresco.<br>Lei lo ha pagato. |
| it_07 | it | en | Tu non sei solo.<br>Noi siamo con te.<br>Lui può aiutarci. |
| it_07 | it | es | Tu non sei solo.<br>Noi siamo con te.<br>Lui può aiutarci. |
| it_07 | it | fr | Tu non sei solo.<br>Noi siamo con te.<br>Lui può aiutarci. |
| it_08 | it | en | La lettera è di mia sorella.<br>Lei ha una casa nuova.<br>Io vado a trovarla. |
| it_08 | it | es | La lettera è di mia sorella.<br>Lei ha una casa nuova.<br>Io vado a trovarla. |
| it_08 | it | fr | La lettera è di mia sorella.<br>Lei ha una casa nuova.<br>Io vado a trovarla. |
| it_08 | it | pt | La lettera è di mia sorella.<br>Lei ha una casa nuova.<br>Io vado a trovarla. |
| it_09 | it | en | Noi abbiamo una riunione.<br>È nel nostro ufficio.<br>Loro vengono anche. |
| it_10 | it | en | Lui ha una piccola barca.<br>Noi possiamo andare con lui.<br>L'acqua è calma. |
| it_10 | it | es | Lui ha una piccola barca.<br>Noi possiamo andare con lui.<br>L'acqua è calma. |
| it_11 | it | en | La lampada è sulla mia scrivania.<br>Io la uso di notte.<br>È molto luminosa. |
| it_12 | it | en | Il nostro cane è nel cortile.<br>Lui ci aspetta.<br>Noi dobbiamo uscire. |
| it_13 | it | en | Io voglio un biglietto.<br>Tu hai i soldi.<br>Noi possiamo comprarlo qui. |
| it_13 | it | es | Io voglio un biglietto.<br>Tu hai i soldi.<br>Noi possiamo comprarlo qui. |
| it_13 | it | fr | Io voglio un biglietto.<br>Tu hai i soldi.<br>Noi possiamo comprarlo qui. |
| it_14 | it | en | Lei ha un cappotto blu.<br>È nella sua borsa.<br>Io glielo porterò. |
| it_15 | it | en | Loro leggono il libro.<br>È sulla nostra città.<br>Noi conosciamo lo scrittore. |
| it_16 | it | en | L'autobus è vicino al ponte.<br>Noi siamo in orario.<br>Tu puoi sederti con me. |
| it_16 | it | es | L'autobus è vicino al ponte.<br>Noi siamo in orario.<br>Tu puoi sederti con me. |
| it_16 | it | fr | L'autobus è vicino al ponte.<br>Noi siamo in orario.<br>Tu puoi sederti con me. |
| it_16 | it | pt | L'autobus è vicino al ponte.<br>Noi siamo in orario.<br>Tu puoi sederti con me. |
| it_17 | it | en | Lui era qui prima.<br>Io non ero con lui.<br>Lei sa dove è andato. |
| it_18 | it | en | Noi facciamo una torta.<br>È per un nostro amico.<br>Loro vengono stasera. |
| it_18 | it | es | Noi facciamo una torta.<br>È per un nostro amico.<br>Loro vengono stasera. |
| it_19 | it | en | Tu puoi prendere la sedia.<br>Io sono sul pavimento.<br>Noi abbiamo spazio. |
| it_19 | it | es | Tu puoi prendere la sedia.<br>Io sono sul pavimento.<br>Noi abbiamo spazio. |
| it_19 | it | fr | Tu puoi prendere la sedia.<br>Io sono sul pavimento.<br>Noi abbiamo spazio. |
| it_20 | it | en | La musica è dolce.<br>Lei suona per noi.<br>Noi la ascoltiamo. |
| de_01 | de | en | Ich habe den Schlüssel.<br>Du kannst die Tür öffnen.<br>Wir sind hier. |
| de_02 | de | en | Sie ist am Bahnhof.<br>Ihr Zug ist verspätet.<br>Sie warten auf uns. |
| de_03 | de | en | Die Suppe steht auf dem Tisch.<br>Er hat sie für dich gemacht.<br>Wir essen jetzt. |
| de_04 | de | en | Unser Lehrer hat eine Karte.<br>Sie ist für die Klasse.<br>Du kannst sie ansehen. |
| de_05 | de | en | Sie sind im Garten.<br>Wir wollen bei ihnen sitzen.<br>Hier ist es ruhig. |
| de_06 | de | en | Ich war im Laden.<br>Das Brot war frisch.<br>Sie hat es bezahlt. |
| de_07 | de | en | Du bist nicht allein.<br>Wir sind bei dir.<br>Er kann uns helfen. |
| de_08 | de | en | Der Brief ist von meiner Schwester.<br>Sie hat ein neues Haus.<br>Ich besuche sie. |
| de_09 | de | en | Wir haben eine Besprechung.<br>Sie ist in unserem Büro.<br>Sie kommen auch. |
| de_10 | de | en | Er hat ein kleines Boot.<br>Wir können mit ihm fahren.<br>Das Wasser ist ruhig. |
| de_11 | de | en | Die Lampe steht auf meinem Tisch.<br>Ich nutze sie am Abend.<br>Sie ist sehr hell. |
| de_12 | de | en | Unser Hund ist im Hof.<br>Er wartet auf uns.<br>Wir müssen hinausgehen. |
| de_13 | de | en | Ich brauche eine Karte.<br>Du hast das Geld.<br>Wir können sie hier kaufen. |
| de_14 | de | en | Sie hat einen blauen Mantel.<br>Er ist in ihrer Tasche.<br>Ich bringe ihn ihr. |
| de_15 | de | en | Sie lesen das Buch.<br>Es handelt von unserer Stadt.<br>Wir kennen den Autor. |
| de_16 | de | en | Der Bus steht an der Brücke.<br>Wir sind pünktlich.<br>Du kannst bei mir sitzen. |
| de_17 | de | en | Er war vorher hier.<br>Ich war nicht bei ihm.<br>Sie weiß wohin er ging. |
| de_18 | de | en | Wir backen einen Kuchen.<br>Er ist für unseren Freund.<br>Sie kommen heute Abend. |
| de_19 | de | en | Du kannst den Stuhl nehmen.<br>Ich sitze auf dem Boden.<br>Wir haben Platz. |
| de_20 | de | en | Die Musik ist leise.<br>Sie spielt für uns.<br>Wir hören ihr zu. |
| nl_01 | nl | en | Ik heb de sleutel.<br>Jij kunt de deur openen.<br>Wij wachten hier. |
| nl_02 | nl | en | Zij is op het station.<br>Haar trein is te laat.<br>Wij wachten op haar. |
| nl_03 | nl | en | De soep staat op de tafel.<br>Hij heeft die voor jou gemaakt.<br>Wij eten nu. |
| nl_04 | nl | en | Onze leraar heeft een kaart.<br>Die is voor de klas.<br>Jij kunt die bekijken. |
| nl_05 | nl | en | Zij zijn in de tuin.<br>Wij willen bij hen zitten.<br>Het is hier rustig. |
| nl_06 | nl | en | Ik was in de winkel.<br>Het brood was vers.<br>Zij heeft het betaald. |
| nl_07 | nl | en | Jij bent niet alleen.<br>Wij zijn bij jou.<br>Hij kan ons helpen. |
| nl_08 | nl | en | De brief is van mijn zus.<br>Zij heeft een nieuw huis.<br>Ik bezoek haar. |
| nl_09 | nl | en | Wij hebben een vergadering.<br>Die is in ons kantoor.<br>Zij komen ook. |
| nl_09 | nl | de | Wij hebben een vergadering.<br>Die is in ons kantoor.<br>Zij komen ook. |
| nl_10 | nl | en | Hij heeft een kleine boot.<br>Wij kunnen met hem varen.<br>Het water is rustig. |
| nl_11 | nl | en | De lamp staat op mijn tafel.<br>Ik gebruik die in de avond.<br>Zij is erg helder. |
| nl_11 | nl | de | De lamp staat op mijn tafel.<br>Ik gebruik die in de avond.<br>Zij is erg helder. |
| nl_12 | nl | en | Onze hond is op de binnenplaats.<br>Hij wacht op ons.<br>Wij moeten naar buiten. |
| nl_13 | nl | en | Ik wil een kaartje.<br>Jij hebt het geld.<br>Wij kunnen het hier kopen. |
| nl_14 | nl | en | Zij heeft een blauwe jas.<br>Die is in haar tas.<br>Ik breng die naar haar. |
| nl_14 | nl | de | Zij heeft een blauwe jas.<br>Die is in haar tas.<br>Ik breng die naar haar. |
| nl_15 | nl | en | Zij lezen het boek.<br>Het gaat over onze stad.<br>Wij kennen de schrijver. |
| nl_16 | nl | en | De bus staat bij de brug.<br>Wij zijn op tijd.<br>Jij kunt bij mij zitten. |
| nl_17 | nl | en | Hij was eerder hier.<br>Ik was niet bij hem.<br>Zij weet waar hij heen ging. |
| nl_18 | nl | en | Wij maken een taart.<br>Die is voor onze vriend.<br>Zij komen vanavond. |
| nl_19 | nl | en | Jij kunt de stoel nemen.<br>Ik zit op de vloer.<br>Wij hebben genoeg ruimte. |
| nl_20 | nl | en | De muziek is zacht.<br>Zij speelt voor ons.<br>Wij luisteren naar haar. |
| pl_01 | pl | en | Mam klucz i czekam.<br>Ty możesz otworzyć drzwi.<br>My jesteśmy tu, a ona tam. |
| pl_01 | pl | es | Mam klucz i czekam.<br>Ty możesz otworzyć drzwi.<br>My jesteśmy tu, a ona tam. |
| pl_01 | pl | fr | Mam klucz i czekam.<br>Ty możesz otworzyć drzwi.<br>My jesteśmy tu, a ona tam. |
| pl_01 | pl | pt | Mam klucz i czekam.<br>Ty możesz otworzyć drzwi.<br>My jesteśmy tu, a ona tam. |
| pl_01 | pl | it | Mam klucz i czekam.<br>Ty możesz otworzyć drzwi.<br>My jesteśmy tu, a ona tam. |
| pl_02 | pl | en | Ona jest na stacji.<br>Jej pociąg jest spóźniony.<br>My czekamy na nią i nie wychodzimy. |
| pl_03 | pl | en | Zupa jest na stole.<br>On zrobił ją dla ciebie.<br>My jemy, a ona nie chce. |
| pl_03 | pl | pt | Zupa jest na stole.<br>On zrobił ją dla ciebie.<br>My jemy, a ona nie chce. |
| pl_04 | pl | en | Nasz nauczyciel ma mapę.<br>To jest dla klasy.<br>Ty możesz ją obejrzeć, ale nie teraz. |
| pl_05 | pl | en | Oni są w ogrodzie.<br>My chcemy być z nimi.<br>Tu jest cicho i nie ma hałasu. |
| pl_05 | pl | it | Oni są w ogrodzie.<br>My chcemy być z nimi.<br>Tu jest cicho i nie ma hałasu. |
| pl_06 | pl | en | Byłam w sklepie.<br>Chleb był świeży.<br>Ona za niego zapłaciła, a ja nie. |
| pl_07 | pl | en | Ty nie jesteś sam.<br>My jesteśmy z tobą.<br>On może nam pomóc, bo jest tu. |
| pl_08 | pl | en | List jest od siostry.<br>Ona ma nowy dom.<br>Ja chcę ją odwiedzić i być z nią. |
| pl_08 | pl | it | List jest od siostry.<br>Ona ma nowy dom.<br>Ja chcę ją odwiedzić i być z nią. |
| pl_09 | pl | en | My mamy spotkanie.<br>To jest w naszym biurze.<br>Oni też przyjdą, ale nie dziś. |
| pl_10 | pl | en | On ma małą łódź.<br>My możemy z nim płynąć.<br>Woda jest spokojna i nie wieje. |
| pl_10 | pl | it | On ma małą łódź.<br>My możemy z nim płynąć.<br>Woda jest spokojna i nie wieje. |
| pl_11 | pl | en | Lampa jest na moim biurku.<br>Ja używam jej w nocy.<br>Ona jest jasna, ale nie duża. |
| pl_12 | pl | en | Nasz pies jest na podwórku.<br>On na nas czeka.<br>My musimy wyjść, bo jest późno. |
| pl_12 | pl | pt | Nasz pies jest na podwórku.<br>On na nas czeka.<br>My musimy wyjść, bo jest późno. |
| pl_13 | pl | en | Ja chcę bilet.<br>Ty masz pieniądze.<br>My możemy go kupić, jeśli sklep jest otwarty. |
| pl_14 | pl | en | Ona ma niebieski płaszcz.<br>To jest w jej torbie.<br>Ja jej go przyniosę, ale nie teraz. |
| pl_15 | pl | en | Oni czytają książkę.<br>To jest o naszym mieście.<br>My znamy autora i jego siostrę. |
| pl_15 | pl | it | Oni czytają książkę.<br>To jest o naszym mieście.<br>My znamy autora i jego siostrę. |
| pl_16 | pl | en | Autobus jest przy moście.<br>My jesteśmy na czas.<br>Ty możesz być ze mną, a ona z nim. |
| pl_16 | pl | pt | Autobus jest przy moście.<br>My jesteśmy na czas.<br>Ty możesz być ze mną, a ona z nim. |
| pl_17 | pl | en | On był tu wcześniej.<br>Ja nie byłam z nim.<br>Ona wie, gdzie był i co robił. |
| pl_17 | pl | it | On był tu wcześniej.<br>Ja nie byłam z nim.<br>Ona wie, gdzie był i co robił. |
| pl_18 | pl | en | My robimy ciasto.<br>To jest dla przyjaciela.<br>Oni przyjdą, gdy będzie gotowe. |
| pl_19 | pl | en | Ty możesz wziąć krzesło.<br>Ja siedzę na podłodze.<br>My mamy miejsce, ale nie dużo. |
| pl_20 | pl | en | Muzyka jest cicha.<br>Ona gra dla nas.<br>My jej słuchamy i nie rozmawiamy. |

Short input/name-only/numbers-only examples are inconclusive for nl/pl by design. A balanced mixed-language example is not accepted as either; no automatic dominant-language inference or confidence percentage is supplied. Mixed paragraphs with a strong majority can still look compatible, so audio evidence and human review remain necessary. No old classifier was tightened to make the matrix look better.
